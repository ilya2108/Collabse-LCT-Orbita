"""Роутер модуля `skills`: справочник отраслей, специализаций, графов и профилей грейдов.

Эндпоинты публичные (справочные данные без ПДн): их читает экран выбора специализации
в опросе, страница «О механике» и редактор подграфа потребности. Источник — YAML-каталог
`app/skills` через `get_catalog()` (кэш в памяти процесса).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.modules.skills import service
from app.modules.skills.schemas import GradeProfileOut, GraphOut, IndustryOut, SpecializationOut
from app.skills.loader import SkillsCatalog, get_catalog

router = APIRouter(prefix="/skills", tags=["skills"])

Catalog = Annotated[SkillsCatalog, Depends(get_catalog)]


@router.get("/industries", response_model=list[IndustryOut], summary="Справочник отраслей")
def industries(catalog: Catalog) -> list[IndustryOut]:
    return service.list_industries(catalog)


@router.get(
    "/specializations",
    response_model=list[SpecializationOut],
    summary="Специализации (active — с тестом, preview — только граф)",
)
def specializations(catalog: Catalog) -> list[SpecializationOut]:
    return service.list_specializations(catalog)


@router.get(
    "/graph/{spec}",
    response_model=GraphOut,
    summary="Граф навыков специализации: слои, узлы, рёбра, мосты",
    responses={404: {"description": "Специализация не найдена"}},
)
def graph(spec: str, catalog: Catalog) -> GraphOut:
    return service.get_graph(spec, catalog)


@router.get(
    "/grade-profiles/{spec}",
    response_model=list[GradeProfileOut],
    summary="Профили грейдов специализации (полные и демо)",
    responses={404: {"description": "Специализация не найдена"}},
)
def grade_profiles(spec: str, catalog: Catalog) -> list[GradeProfileOut]:
    return service.get_grade_profiles(spec, catalog)
