"""Сид справочника навыков: YAML из `app/skills` → таблицы `industry`, `specialization`,
`skill_node`, `skill_edge`, `grade_profile`, `fsp_discipline_map` (PLAN.md §3.2, §3.4, §3.5).

Источник правды — YAML (загружается и валидируется `app.skills.loader`), БД — зеркало для
внешних ключей (`candidate_profile.industry_code`, `skill_confirmation.node_id`,
`candidate_category.specialization_code`) и SQL-выборок подбора. Сид идемпотентен: upsert по
натуральным ключам (`code`, `id`, `(source_id, target_id)`, `(specialization_code, grade)`,
`(discipline, event_type)`), повторный запуск ничего не дублирует. Рёбра, исчезнувшие из YAML,
удаляются; узлы и специализации — нет (на них ссылаются подтверждения и категории).

Подключается каркасом `app.tools.seed` (модуль `skills` — первый в `SEED_ORDER`).
Чистая часть (`collect_rows`) не зависит от БД и покрыта тестами без PostgreSQL.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skills import (
    FspDisciplineMap,
    GradeProfile,
    Industry,
    SkillEdge,
    SkillNode,
    Specialization,
)
from app.skills.loader import DisciplineDef, SkillsCatalog, get_catalog

Rows = dict[str, list[dict[str, Any]]]

# тип события ФСП по умолчанию: правило дисциплины действует для любого события
ANY_EVENT = "*"


def _discipline_row(discipline: DisciplineDef) -> dict[str, Any]:
    """Строка `fsp_discipline_map` из определения дисциплины YAML.

    `node_weights`: для `node_scope: fixed` — `{node_id, weight}`, для
    `candidate_specialization` — `{node_code, weight}` (узел берётся в специализации кандидата).
    `level_rule`: правило уровня (`by: percentile|tier`) плюс метаданные сопоставления
    (`node_scope`, `specializations`, `match`, `title`) — отдельных колонок под них нет.
    """
    node_weights = [
        {"node_id": ref.id, "weight": ref.weight}
        if ref.id is not None
        else {"node_code": ref.code, "weight": ref.weight}
        for ref in discipline.nodes
    ]
    level_rule = discipline.level.model_dump(exclude_none=True)
    level_rule.update(
        {
            "node_scope": discipline.node_scope,
            "specializations": list(discipline.specializations),
            "match": list(discipline.match),
            "title": discipline.title,
        }
    )
    specialization_code = discipline.fallback_specialization
    if specialization_code is None and discipline.node_scope == "fixed":
        # все узлы fixed-дисциплины лежат в одной специализации — берём её из первого id
        first = next((ref.id for ref in discipline.nodes if ref.id), None)
        specialization_code = first.split(".", 1)[0] if first else None
    return {
        "discipline": discipline.code,
        "event_type": ANY_EVENT,
        "specialization_code": specialization_code,
        "node_weights": node_weights,
        "level_rule": level_rule,
    }


def collect_rows(catalog: SkillsCatalog) -> Rows:
    """Строки для всех шести таблиц в порядке вставки (справочники → зависимые)."""
    industries = [
        {"code": item.code, "title": item.title, "sort_order": index * 10}
        for index, item in enumerate(catalog.industries, start=1)
    ]
    specializations: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    profiles: list[dict[str, Any]] = []
    # порядок каталога: active раньше preview — сохраняем его в sort_order
    for index, (code, spec) in enumerate(catalog.specializations.items(), start=1):
        meta = spec.file.specialization
        specializations.append(
            {
                "code": code,
                "title": meta.title,
                "status": meta.status,
                "graph_version": meta.graph_version,
                "sort_order": index * 10,
            }
        )
        for position, node in enumerate(spec.file.nodes, start=1):
            nodes.append(
                {
                    "id": node.id,
                    "specialization_code": code,
                    "code": node.code,
                    "layer": node.layer,
                    "title": node.title,
                    "description": node.description,
                    "sort_order": position * 10,
                }
            )
        edges.extend(
            {"source_id": edge.source, "target_id": edge.target, "kind": edge.kind}
            for edge in spec.file.edges
        )
        for profile in catalog.profiles(code):
            profiles.append(
                {
                    "specialization_code": code,
                    "grade": profile.grade,
                    "target_nodes": list(profile.target_nodes),
                    "task_level": profile.task_level,
                    "min_active": profile.min_active,
                    "min_q": profile.min_q,
                    "constraints": profile.constraints.as_dict(),
                    "budget": profile.budget.model_dump(),
                    "demo": {
                        "target_nodes": list(profile.demo.target_nodes),
                        "min_active": profile.demo.min_active,
                        "min_q": profile.demo.min_q,
                        "constraints": profile.demo.constraints.as_dict(),
                    },
                }
            )
    # мосты объявлены один раз в graph.yaml специализации-источника; в БД — обычные рёбра bridge
    edges.extend(
        {"source_id": bridge.source, "target_id": bridge.target, "kind": "bridge"}
        for bridge in catalog.bridges
    )
    disciplines = [_discipline_row(item) for item in catalog.fsp_mapping.disciplines]
    return {
        "industry": industries,
        "specialization": specializations,
        "skill_node": nodes,
        "skill_edge": edges,
        "grade_profile": profiles,
        "fsp_discipline_map": disciplines,
    }


async def _upsert(
    session: AsyncSession,
    model: type[Any],
    rows: list[dict[str, Any]],
    conflict_columns: list[str],
) -> int:
    """INSERT … ON CONFLICT DO UPDATE по натуральному ключу; возвращает число строк."""
    if not rows:
        return 0
    stmt = pg_insert(model).values(rows)
    update_columns = {
        column: getattr(stmt.excluded, column)
        for column in rows[0]
        if column not in conflict_columns
    }
    if update_columns:
        stmt = stmt.on_conflict_do_update(index_elements=conflict_columns, set_=update_columns)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=conflict_columns)
    await session.execute(stmt)
    return len(rows)


async def seed(session: AsyncSession) -> dict[str, int]:
    """Шаг сида для `app.tools.seed`: грузит каталог YAML в БД, commit — на вызывающей стороне."""
    catalog = get_catalog()
    rows = collect_rows(catalog)
    report = {
        "industries": await _upsert(session, Industry, rows["industry"], ["code"]),
        "specializations": await _upsert(session, Specialization, rows["specialization"], ["code"]),
        "nodes": await _upsert(session, SkillNode, rows["skill_node"], ["id"]),
        "edges": await _upsert(session, SkillEdge, rows["skill_edge"], ["source_id", "target_id"]),
        "profiles": await _upsert(
            session, GradeProfile, rows["grade_profile"], ["specialization_code", "grade"]
        ),
        "fsp_disciplines": await _upsert(
            session, FspDisciplineMap, rows["fsp_discipline_map"], ["discipline", "event_type"]
        ),
    }
    # рёбра, которых больше нет в YAML (внутри загруженных специализаций), удаляем
    seeded_specs = [row["code"] for row in rows["specialization"]]
    keep = {(row["source_id"], row["target_id"]) for row in rows["skill_edge"]}
    result = await session.execute(
        sa.select(SkillEdge.source_id, SkillEdge.target_id).where(
            sa.or_(
                *(
                    SkillEdge.source_id.like(f"{code}.%") | SkillEdge.target_id.like(f"{code}.%")
                    for code in seeded_specs
                )
            )
        )
    )
    stale = [pair for pair in result.all() if tuple(pair) not in keep]
    for source_id, target_id in stale:
        await session.execute(
            sa.delete(SkillEdge).where(
                SkillEdge.source_id == source_id, SkillEdge.target_id == target_id
            )
        )
    report["edges_removed"] = len(stale)
    return report
