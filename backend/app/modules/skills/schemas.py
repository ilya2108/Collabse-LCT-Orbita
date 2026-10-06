"""Pydantic-схемы ответов модуля skills (контракт API, CLAUDE.md / PLAN.md §7.5)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Layer = Literal["D", "B", "C"]
EdgeKind = Literal["vertical", "lateral", "bridge"]
SpecStatus = Literal["active", "preview"]
Grade = Literal["junior", "middle", "senior"]


class IndustryOut(BaseModel):
    code: str
    title: str


class SpecializationOut(BaseModel):
    code: str
    title: str
    status: SpecStatus
    graph_version: str
    node_count: int


class SpecializationMetaOut(BaseModel):
    code: str
    title: str
    status: SpecStatus
    graph_version: str


class LayerOut(BaseModel):
    code: Layer
    title: str


class NodeOut(BaseModel):
    id: str = Field(examples=["be.D1"])
    code: str = Field(examples=["D1"])
    layer: Layer
    title: str
    description: str


class EdgeOut(BaseModel):
    source: str
    target: str
    kind: EdgeKind


class GraphOut(BaseModel):
    specialization: SpecializationMetaOut
    layers: list[LayerOut]
    nodes: list[NodeOut]
    edges: list[EdgeOut]
    # мосты в другие специализации (kind всегда bridge), включая входящие
    bridges: list[EdgeOut]


class BudgetOut(BaseModel):
    min_items: int
    max_items: int


class DemoProfileOut(BaseModel):
    target_nodes: list[str]
    min_active: int
    min_q: int
    constraints: dict[str, Any]


class GradeProfileOut(BaseModel):
    grade: Grade
    target_nodes: list[str]
    task_level: Literal[1, 2, 3]
    min_active: int
    min_q: int
    constraints: dict[str, Any]
    budget: BudgetOut
    demo: DemoProfileOut


class UnsupportedHitOut(BaseModel):
    """Попадание в направление вне MVP (§5.1)."""

    code: str
    title: str
    nearest: str
    keywords: list[str]


class KeywordMatchOut(BaseModel):
    """Результат разбора текста словарём (режим rules): попадания по узлам и специализациям."""

    nodes: dict[str, list[str]]  # node_id → найденные ключевые слова
    specialization_scores: dict[str, int]  # spec → суммарное число попаданий
    specialization: str | None  # лучшая специализация или None
    unsupported: list[UnsupportedHitOut]
