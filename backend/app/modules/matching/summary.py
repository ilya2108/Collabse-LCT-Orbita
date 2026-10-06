"""Саммари картины по кандидатам на потребность человеческим языком
(`GET /employers/needs/{need_id}/selection-summary`).

Вход для промпта `selection_summary` — только обезличенные данные: узлы потребности
с важностью must/should/nice и топ-15 кандидатов выдачи (псевдоним, грейд, покрытие,
закрытые и незакрытые must-узлы) — ПДн в LLM не передаются никогда. При любой ошибке LLM
текст собирается детерминированным rules-фолбэком (статистика выдачи: сколько закрывают
все must, самый дефицитный узел — кандидат на компромисс), эндпоинт не падает.
Ответ кэшируется на час по ключу (потребность, версия подборки).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_cache
from app.core.errors import not_found
from app.models.employer import EmployerNeed
from app.models.skills import GradeProfile
from app.modules.llm.schemas import SummaryText
from app.modules.llm.service import get_llm_service
from app.modules.matching.schemas import RankedCandidateOut, SelectionSummaryOut
from app.modules.matching.service import (
    NeedNode,
    collect_candidates,
    latest_verified_version,
    need_nodes_from_subgraph,
    rank_candidates,
)
from app.skills.loader import get_catalog as get_skills_catalog

logger = logging.getLogger("app.matching.summary")

CACHE_TTL_SECONDS = 3600
TOP_CANDIDATES = 15


async def build_selection_summary(
    session: AsyncSession, need: EmployerNeed, company_id: Any
) -> SelectionSummaryOut:
    """Сводка по подборке: версия подборки → кэш → узлы → выдача → LLM (или rules-фолбэк).
    404 — если у потребности нет подграфа навыков (ни версии, ни профиля грейда).

    Порядок запросов фиксирован (важно для тестов): версия → профиль грейда (если нет
    подграфа) → категории → профили → снимки узлов → уровни целостности."""
    version = await latest_verified_version(session, need.id)
    cache_suffix = f"selection-summary:{need.id}:{version.version if version else 'none'}"
    cache = get_cache()
    cached = await cache.get_json(cache_suffix)
    if cached is not None:
        return SelectionSummaryOut(**cached)

    nodes = need_nodes_from_subgraph(version.subgraph if version else None)
    if not nodes and need.specialization_code and need.grade:
        profile = (
            await session.execute(
                select(GradeProfile).where(
                    GradeProfile.specialization_code == need.specialization_code,
                    GradeProfile.grade == need.grade,
                )
            )
        ).scalar_one_or_none()
        if profile is not None and profile.target_nodes:
            nodes = [NeedNode(id=n) for n in profile.target_nodes]
    if not nodes:
        raise not_found("Подборка ещё не сформирована: у потребности нет подграфа навыков")

    candidates = await collect_candidates(session, need, company_id, [n.id for n in nodes])
    items = rank_candidates(nodes, need.grade, candidates)[:TOP_CANDIDATES]

    need_facts, candidate_facts, must_ids = _collect_facts(need, nodes, items)
    llm = get_llm_service()
    try:
        result = await llm.complete(
            "selection_summary",
            {"need": need_facts, "candidates": candidate_facts},
            schema=SummaryText,
            session=None,
        )
        parsed = result.parsed
        if not isinstance(parsed, SummaryText):  # устаревший кэш другой схемы
            raise TypeError(f"selection_summary вернул {type(parsed).__name__}")
        summary, source = parsed.text, "llm"
    except Exception as exc:  # noqa: BLE001 — сводка не должна ронять эндпоинт
        logger.warning("selection_summary: ошибка LLM, rules-фолбэк: %s", exc)
        summary, source = rules_selection_summary(items, must_ids), "rules"

    out = SelectionSummaryOut(summary=summary, source=source, generated_at=datetime.now(UTC))
    await cache.set_json(cache_suffix, out.model_dump(mode="json"), CACHE_TTL_SECONDS)
    return out


def _node_title(node_id: str) -> str:
    """Название узла из справочника; узел вне справочника — по коду."""
    try:
        return get_skills_catalog().node(node_id).title
    except KeyError:
        return node_id


def _collect_facts(
    need: EmployerNeed, nodes: list[NeedNode], items: list[RankedCandidateOut]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Обезличенные факты для промпта: узлы потребности и топ кандидатов (псевдонимы)."""
    must_ids = [n.id for n in nodes if n.importance == "must"]
    need_facts: dict[str, Any] = {
        "title": need.title,
        "specialization": need.specialization_code,
        "grade": need.grade,
        "nodes": [
            {
                "id": n.id,
                "title": _node_title(n.id),
                "importance": n.importance,
                "min_level": n.min_level,
            }
            for n in nodes
        ],
    }
    candidate_facts: list[dict[str, Any]] = []
    for item in items:
        matched = set(item.explanation.matched_nodes)
        candidate_facts.append(
            {
                "pseudonym": item.pseudonym,
                "grade": item.grade,
                "grade_confirmed": item.grade_confirmed,
                "coverage_pct": round((item.coverage or 0.0) * 100),
                "must_closed": [_node_title(i) for i in must_ids if i in matched],
                "must_missing": [_node_title(i) for i in must_ids if i not in matched],
            }
        )
    return need_facts, candidate_facts, must_ids


def rules_selection_summary(items: list[RankedCandidateOut], must_ids: list[str]) -> str:
    """Детерминированный фолбэк: статистика выдачи без LLM."""
    total = len(items)
    if total == 0:
        return (
            "Кандидатов по специализации потребности в выдаче нет — недобор. "
            "Стоит расширить требования (понизить часть must-узлов до should) "
            "или подождать новых подтверждённых профилей."
        )
    closed_by: dict[str, int] = {
        node_id: sum(1 for item in items if node_id in item.explanation.matched_nodes)
        for node_id in must_ids
    }
    full_must = sum(
        1
        for item in items
        if all(node_id in item.explanation.matched_nodes for node_id in must_ids)
    )
    p1 = f"В выдаче {total} кандидатов; все must-узлы закрывают {full_must} из них."
    if must_ids:
        deficit_id = min(closed_by, key=lambda node_id: (closed_by[node_id], node_id))
        deficit_count = closed_by[deficit_id]
        if deficit_count == 0:
            p2 = (
                f"Ни один кандидат не закрывает узел «{_node_title(deficit_id)}» — "
                "главный кандидат на компромисс: понизить с must до should "
                "и проверять на собеседовании."
            )
        else:
            p2 = (
                f"Самый дефицитный узел — «{_node_title(deficit_id)}»: его закрывают "
                f"{deficit_count} из {total} кандидатов; если воронка узкая — это "
                "кандидат на компромисс (must → should)."
            )
    else:
        p2 = "У потребности нет must-узлов — покрытие считается по should/nice."
    overqualified = sum(1 for item in items if (item.coverage or 0.0) >= 0.999)
    if full_must == 0:
        p3 = (
            "Недобор: полностью требования не закрывает никто — смотреть стоит сегмент "
            "с максимальным покрытием и закладывать время на дообучение."
        )
    elif overqualified >= 3:
        p3 = (
            f"{overqualified} кандидатов закрывают подграф полностью — возможен оверфит: "
            "проверьте вилку зарплаты и мотивацию, иначе риск скуки и быстрого ухода."
        )
    else:
        p3 = (
            "Баланс приемлемый: начните с кандидатов с полным покрытием must-узлов, "
            "частичное покрытие — резервный сегмент."
        )
    return "\n\n".join([p1, p2, p3])
