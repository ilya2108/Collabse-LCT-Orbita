"""Сервис справочника навыков: выдача графов и профилей из каталога, разбор текста словарём.

Данные приходят из `app.skills.loader.get_catalog()` (YAML, кэш в памяти); БД не нужна —
справочник в таблицах `specialization`/`skill_node`/… появляется сидом и служит для
внешних ключей, а источник правды остаётся в YAML.
"""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache

from app.core.errors import not_found
from app.modules.skills.schemas import (
    BudgetOut,
    DemoProfileOut,
    EdgeOut,
    GradeProfileOut,
    GraphOut,
    IndustryOut,
    KeywordMatchOut,
    LayerOut,
    NodeOut,
    SpecializationMetaOut,
    SpecializationOut,
    UnsupportedHitOut,
)
from app.skills.loader import SkillsCatalog, SpecGraph, get_catalog


def _require_spec(catalog: SkillsCatalog, spec_code: str) -> SpecGraph:
    spec = catalog.spec(spec_code)
    if spec is None:
        raise not_found(f"Специализация «{spec_code}» не найдена")
    return spec


def list_industries(catalog: SkillsCatalog | None = None) -> list[IndustryOut]:
    catalog = catalog or get_catalog()
    return [IndustryOut(code=item.code, title=item.title) for item in catalog.industries]


def list_specializations(catalog: SkillsCatalog | None = None) -> list[SpecializationOut]:
    catalog = catalog or get_catalog()
    return [
        SpecializationOut(
            code=spec.meta.code,
            title=spec.meta.title,
            status=spec.meta.status,
            graph_version=spec.meta.graph_version,
            node_count=len(spec.file.nodes),
        )
        for spec in catalog.specializations.values()
    ]


def get_graph(spec_code: str, catalog: SkillsCatalog | None = None) -> GraphOut:
    catalog = catalog or get_catalog()
    spec = _require_spec(catalog, spec_code)
    return GraphOut(
        specialization=SpecializationMetaOut(
            code=spec.meta.code,
            title=spec.meta.title,
            status=spec.meta.status,
            graph_version=spec.meta.graph_version,
        ),
        layers=[LayerOut(code=layer.code, title=layer.title) for layer in spec.file.layers],
        nodes=[
            NodeOut(
                id=node.id,
                code=node.code,
                layer=node.layer,
                title=node.title,
                description=node.description,
            )
            for node in spec.file.nodes
        ],
        edges=[
            EdgeOut(source=edge.source, target=edge.target, kind=edge.kind)
            for edge in spec.file.edges
        ],
        bridges=[
            EdgeOut(source=bridge.source, target=bridge.target, kind="bridge")
            for bridge in catalog.bridges_for(spec_code)
        ],
    )


def get_grade_profiles(
    spec_code: str, catalog: SkillsCatalog | None = None
) -> list[GradeProfileOut]:
    """Профили грейдов специализации; для preview-специализаций без теста — пустой список."""
    catalog = catalog or get_catalog()
    _require_spec(catalog, spec_code)
    return [
        GradeProfileOut(
            grade=profile.grade,
            target_nodes=list(profile.target_nodes),
            task_level=profile.task_level,
            min_active=profile.min_active,
            min_q=profile.min_q,
            constraints=profile.constraints.as_dict(),
            budget=BudgetOut(
                min_items=profile.budget.min_items, max_items=profile.budget.max_items
            ),
            demo=DemoProfileOut(
                target_nodes=list(profile.demo.target_nodes),
                min_active=profile.demo.min_active,
                min_q=profile.demo.min_q,
                constraints=profile.demo.constraints.as_dict(),
            ),
        )
        for profile in catalog.profiles(spec_code)
    ]


# --- режим rules: словарь «ключевое слово → узел» (§5.1) ---------------------------------


@lru_cache(maxsize=4096)
def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Слово совпадает с началом слова в тексте; короткие (≤ 3 символов) и содержащие
    знаки («c#», «ci/cd») — только целиком."""
    escaped = re.escape(keyword.lower())
    whole = len(keyword) <= 3 or not re.fullmatch(r"[\w\s-]+", keyword)
    tail = r"(?!\w)" if whole else ""
    return re.compile(rf"(?<!\w){escaped}{tail}")


def _hits(text: str, keywords: list[str]) -> list[str]:
    return [keyword for keyword in keywords if _keyword_pattern(keyword).search(text)]


def match_keywords(text: str, catalog: SkillsCatalog | None = None) -> KeywordMatchOut:
    """Детерминированный разбор текста потребности без LLM: какие узлы упомянуты,
    какая специализация вероятнее, не описано ли направление вне MVP."""
    catalog = catalog or get_catalog()
    lowered = text.lower()
    words = catalog.keywords

    node_hits = {
        node_id: hits
        for node_id, keywords in words.nodes.items()
        if (hits := _hits(lowered, keywords))
    }
    scores: Counter[str] = Counter()
    for spec_code, keywords in words.specializations.items():
        scores[spec_code] += len(_hits(lowered, keywords))
    for node_id, hits in node_hits.items():
        scores[node_id.split(".", 1)[0]] += len(hits)
    scores = Counter({spec: score for spec, score in scores.items() if score > 0})
    best = max(scores, key=lambda spec: (scores[spec], spec == "be"), default=None)

    unsupported = [
        UnsupportedHitOut(code=item.code, title=item.title, nearest=item.nearest, keywords=hits)
        for item in words.unsupported_specializations
        if (hits := _hits(lowered, item.keywords))
    ]
    return KeywordMatchOut(
        nodes=node_hits,
        specialization_scores=dict(scores),
        specialization=best,
        unsupported=unsupported,
    )
