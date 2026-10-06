"""Pydantic-схемы ответов модуля `assessment`: каталог шаблонов и сигналы целостности.

`TemplateMeta` — метаданные шаблона для админки и инструментов: без условия, кода, опций,
ключа и эталонного ответа (`anchor_variant` целиком остаётся в YAML и в генераторе пула).

Телеметрия раннера (`EventIn`): ПДн-минимизация на уровне контракта — payload принимает
ТОЛЬКО длины и тайминги, лишние ключи (включая любые тексты ответов) отбрасываются
при валидации и в БД не попадают.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.assessment.templates import (
    DifficultyAnchor,
    RubricCriterion,
    TaskTemplate,
    TemplateKind,
    TemplateStatus,
)

EventKind = Literal["paste", "blur", "focus", "item_shown", "first_input", "answer_submitted"]
IntegrityLevel = Literal["high", "medium", "low"]
FlagCode = Literal["large_paste", "tab_blur", "fast_answer", "no_edit"]


class ReviewOut(BaseModel):
    by: str | None = None
    at: date | None = None
    notes: str | None = None


class TemplateMeta(BaseModel):
    """Метаданные шаблона без ответов (anchor_variant скрыт, кроме оценки времени)."""

    id: str
    spec: str
    node: str
    level: int = Field(ge=1, le=3)
    kind: TemplateKind
    title: str
    concept: str
    invariant: str
    vary: str
    forbidden_changes: str
    trap: str
    defect_class: str | None = None
    stack_tags: list[str]
    rubric: list[RubricCriterion]
    difficulty_anchor: DifficultyAnchor
    max_time_sec: int
    max_answer_chars: int
    estimated_minutes: int
    deterministic_share: float = Field(ge=0, le=1)
    status: TemplateStatus
    author: str | None = None
    review: ReviewOut | None = None

    @classmethod
    def from_template(cls, template: TaskTemplate) -> TemplateMeta:
        review = template.review
        return cls(
            id=template.id,
            spec=template.spec,
            node=template.node,
            level=template.level,
            kind=template.kind,
            title=template.title,
            concept=template.concept,
            invariant=template.invariant,
            vary=template.vary,
            forbidden_changes=template.forbidden_changes,
            trap=template.trap,
            defect_class=template.defect_class,
            stack_tags=list(template.stack_tags),
            rubric=list(template.rubric),
            difficulty_anchor=template.difficulty_anchor,
            max_time_sec=template.max_time_sec,
            max_answer_chars=template.max_answer_chars,
            estimated_minutes=template.anchor_variant.estimated_minutes,
            deterministic_share=template.deterministic_share,
            status=template.status,
            author=template.author,
            review=ReviewOut(by=review.by, at=review.at, notes=review.notes) if review else None,
        )


class TemplateListResponse(BaseModel):
    items: list[TemplateMeta]
    total: int
    # предупреждения загрузчика (например, нет graph.yaml — проверка узлов пропущена)
    warnings: list[str] = Field(default_factory=list)


# --- сигналы целостности (телеметрия раннера теста) --------------------------------------


class EventPayloadIn(BaseModel):
    """Только длины и тайминги; незнакомые ключи (в т. ч. тексты) отбрасываются."""

    model_config = ConfigDict(extra="ignore")

    item_id: str | None = Field(default=None, max_length=64)
    len: int | None = Field(default=None, ge=0, le=1_000_000)
    duration_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    chars_typed: int | None = Field(default=None, ge=0, le=1_000_000)
    chars_pasted: int | None = Field(default=None, ge=0, le=1_000_000)


class EventIn(BaseModel):
    kind: EventKind
    client_ts: datetime
    payload: EventPayloadIn = Field(default_factory=EventPayloadIn)


class EventsBatchIn(BaseModel):
    """Батч телеметрии; вместимость проверяется в роутере (413 при превышении)."""

    events: list[EventIn]


class EventsAcceptedOut(BaseModel):
    accepted: int


class IntegrityFlagOut(BaseModel):
    code: FlagCode
    title: str
    count: int
    detail: str


class IntegrityOut(BaseModel):
    """Агрегат целостности сессии: high — 0 флагов, medium — 1–2, low — 3+."""

    level: IntegrityLevel
    flags: list[IntegrityFlagOut]
    events_total: int
    computed_at: datetime


# --- сессии теста (контракт интегратора 11.10) --------------------------------------------

Grade = Literal["junior", "middle", "senior"]
ItemKind = Literal["text", "choice"]
NodeState = Literal["fresh", "fading", "expired", "off"]
TestMode = Literal["express", "full"]


class SessionCreateIn(BaseModel):
    """`mode` необязателен: express — ≤5 заданий по демо-профилю, full — одно задание
    на каждый узел полного T-профиля грейда; без него — поведение по TEST_DEMO_MODE."""

    target_grade: Grade
    mode: TestMode | None = None


class SessionOut(BaseModel):
    """Сессия в API: статус активной сессии — «active» (в БД — in_progress);
    `mode` — из details сессии (None у сессий, созданных до появления режимов)."""

    id: UUID
    status: str
    specialization: str
    target_grade: str
    mode: TestMode | None = None
    items_total: int
    current_index: int


class ItemOptionOut(BaseModel):
    id: str
    text: str


class ItemBodyOut(BaseModel):
    """Задание без эталонов и рубрик; options только у kind=choice."""

    id: UUID
    node_code: str
    title: str
    body_md: str
    kind: ItemKind
    options: list[ItemOptionOut] | None = None
    time_limit_seconds: int | None = None


class ItemCurrentOut(BaseModel):
    index: int
    total: int
    item: ItemBodyOut


class AnswerIn(BaseModel):
    """Ответ кандидата: text — для открытых заданий, choice_id — для закрытых."""

    text: str | None = Field(default=None, max_length=3000)
    choice_id: str | None = Field(default=None, max_length=8)


class AnswerOut(BaseModel):
    accepted: bool = True
    finished: bool
    next_index: int | None = None


class FinishNodeOut(BaseModel):
    code: str
    title: str
    score: float = Field(ge=0, le=1)
    state: NodeState


class FinishIntegrityOut(BaseModel):
    level: IntegrityLevel
    flags: list[IntegrityFlagOut]


class FinishOut(BaseModel):
    """Итог сессии: грейд, Q, узлы со состояниями и агрегат целостности (null — нет данных)."""

    status: str = "graded"
    grade: str | None = None
    grade_confirmed: bool
    q_level: int
    nodes: list[FinishNodeOut]
    integrity: FinishIntegrityOut | None = None


# --- разбор последней завершённой сессии (review) ------------------------------------------

ReviewItemKind = Literal["choice", "text", "skipped"]


class ReviewCriterionOut(BaseModel):
    """Критерий рубрики в разборе: текст, попадание (0 / 0.5 / 1) и цитата-подтверждение."""

    id: str
    text: str
    hit: float = Field(ge=0, le=1)
    evidence: str = ""


class ReviewItemOut(BaseModel):
    """Задание в разборе: без эталонного ответа и ключей choice (банк заданий закрытый).

    `llm_fallback` — открытый ответ оценён эвристикой по ключевым словам (LLM был
    недоступен): обратная связь по критериям менее подробная."""

    node_code: str
    node_title: str
    title: str
    kind: ReviewItemKind
    score: float = Field(ge=0, le=1)
    answered: bool
    correct: bool | None = None
    criteria: list[ReviewCriterionOut] = Field(default_factory=list)
    feedback_short: str = ""
    llm_fallback: bool = False


class SessionReviewOut(BaseModel):
    """Разбор последней завершённой сессии: что проверялось, где ошибки, что улучшить.

    Имя SessionReviewOut (не ReviewOut): ReviewOut уже занят метаданными вычитки шаблона."""

    session_id: UUID
    finished_at: datetime | None = None
    mode: TestMode | None = None
    target_grade: str
    outcome: str | None = None
    nodes: list[FinishNodeOut]
    items: list[ReviewItemOut]
