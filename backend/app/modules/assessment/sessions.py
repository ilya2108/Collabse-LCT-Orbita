"""Сессии теста на грейд — минимальный core пути кандидата (PLAN.md §4.3–4.4, §9).

Контракт интегратора (11.10): POST /assessment/sessions, GET /assessment/sessions/current,
GET .../items/current, POST .../items/{id}/answer, POST .../finish,
GET /assessment/sessions/last/review. Статус сессии в API — «active» (в БД — in_progress).
Эталоны и ключи наружу не отдаются; в review видны только тексты критериев рубрики.

finish идемпотентен: итог сохраняется в `test_session.details["result"]`, повторный finish
по завершённой сессии возвращает его (а не 409) — ретрай фронта после таймаута прокси
(LLM-грейдинг дольше 120 с) получает тот же результат. Параллельный finish отсечён этапом
stage=grading, зафиксированным commit'ом до LLM-цикла (409 `grading_in_progress`).

Как собирается сессия: по узлам профиля грейда (`grade_profiles.yaml`). Режим задаёт
необязательное поле `mode` запроса: express — демо-профиль, не более 5 заданий;
full — одно задание на каждый узел полного T-профиля (потолок FULL_MAX_ITEMS, узлы без
шаблонов пропускаются с warning в details); без `mode` — по TEST_DEMO_MODE (старое
поведение). Шаблоны подбираются из YAML-каталога (`templates.py`):
сначала точный уровень, затем ближайший; `status: draft` допускается с пометкой в
`test_session.details`. Для каждого задания создаётся снапшот эталонного варианта
(`task_variant` со status=retired, retired_reason=session_anchor, вне общего пула) —
раннер и грейдинг не зависят от генерации пула и LLM.

Грейдинг на finish: закрытые (choice) — по ключу шаблона; открытые (text) — `answer_grade`
через LLM-сервис ПОСЛЕДОВАТЕЛЬНО (на стенде gigachat с конкурентностью 1), при любой
ошибке LLM — эвристика rules (`grade_by_keywords`), сессия не падает. Дальше: скоры узлов →
подтверждения (w0 по ступеням graph.yaml, в демо ×demo_multiplier) → candidate_skill_state →
проверка профиля грейда (`strength.check_grade_profile`) → `candidate_category` +
`grade_history`; сессия становится finished (её подхватит integrity_level в подборке).

Порядок запросов к БД фиксирован и задокументирован у каждой функции — тесты роутеров
работают через FakeSession со сценарием ответов.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, conflict, not_found, validation_error
from app.models.assessment import GradeHistory, TaskVariant, TestItem, TestSession
from app.models.assessment import TaskTemplate as TaskTemplateRow
from app.models.candidate import (
    CandidateCategory,
    CandidateProfile,
    CandidateSkillState,
    SkillConfirmation,
)
from app.models.user import AppUser
from app.modules.assessment.config import get_integrity_settings
from app.modules.assessment.integrity import recompute_session_integrity
from app.modules.assessment.templates import TaskTemplate as TemplateDef
from app.modules.assessment.templates import TemplateCatalog
from app.modules.assessment.templates import get_catalog as get_template_catalog
from app.modules.candidates.service import find_profile
from app.modules.llm.providers.stub import grade_by_keywords
from app.modules.llm.schemas import Grading
from app.modules.llm.service import LLMService, get_llm_service
from app.modules.skills.strength import (
    Confirmation,
    check_grade_profile,
    half_life_for,
    is_confident_pass,
    node_state,
    profile_shape,
    w0_for_score,
)
from app.skills.loader import GradeProfileDef, SkillsCatalog, SpecGraph
from app.skills.loader import get_catalog as get_skills_catalog

logger = logging.getLogger("app.assessment.sessions")

DEMO_MAX_ITEMS = 5
# разумный потолок полного режима: одно задание на узел T (сейчас |T| ≤ 10)
FULL_MAX_ITEMS = 12
MODE_EXPRESS = "express"
MODE_FULL = "full"
API_STATUS_ACTIVE = "active"
SESSION_TTL_HOURS = 24
UPGRADE_WINDOW_HOURS = 48
SERVED_FLAG = "served"
LATE_FLAG = "late"
UNANSWERED_FLAG = "unanswered"
LLM_FALLBACK_FLAG = "llm_fallback"
# этап «LLM-грейдинг идёт»: фиксируется в БД до цикла оценки (защита от двойного запуска)
STAGE_GRADING = "grading"
CONFIRMED_SCORE = 0.7  # балл, с которого стек задания попадает в «подтверждённый стек»

NODE_STATES = ("fresh", "fading", "expired", "off")


# --- каталоги (обёртки, подменяемые в тестах) ---------------------------------------------


def template_catalog() -> TemplateCatalog:
    return get_template_catalog()


def skills_catalog() -> SkillsCatalog:
    return get_skills_catalog()


# --- план сессии ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionPlan:
    """Что войдёт в сессию: шаблон на узел (в порядке узлов профиля) и пометки."""

    spec_code: str
    target_grade: str
    demo: bool
    profile: GradeProfileDef
    templates: tuple[TemplateDef, ...]
    warnings: tuple[str, ...]
    draft_template_ids: tuple[str, ...]


def select_template(catalog: TemplateCatalog, node_id: str, level: int) -> TemplateDef | None:
    """Шаблон для узла: reviewed раньше draft, точный уровень раньше ближайшего.

    `scenario_personal` в минимальном core не используется (строится из свершений).
    """
    candidates = [
        t for t in catalog.templates if t.node == node_id and t.kind != "scenario_personal"
    ]
    if not candidates:
        return None
    reviewed = [t for t in candidates if t.status == "reviewed"]
    pool = reviewed or candidates
    exact = [t for t in pool if t.level == level]
    pool = exact or pool
    pool.sort(key=lambda t: (abs(t.level - level), t.level, t.id))
    return pool[0]


def build_plan(
    spec_code: str,
    target_grade: str,
    *,
    demo: bool,
    templates: TemplateCatalog,
    skills: SkillsCatalog,
) -> SessionPlan:
    """Узлы (демо-)профиля грейда → по одному шаблону на узел; в демо (экспресс) — не более
    5 заданий, в полном режиме — один на каждый узел T (потолок FULL_MAX_ITEMS)."""
    profile = skills.profile(spec_code, target_grade)
    if profile is None:
        raise validation_error(
            "Нет профиля грейда для специализации",
            [{"field": "target_grade", "code": "unknown", "message": "junior | middle | senior"}],
        )
    nodes = list(profile.demo.target_nodes if demo else profile.target_nodes)
    nodes = nodes[:DEMO_MAX_ITEMS] if demo else nodes[:FULL_MAX_ITEMS]
    chosen: list[TemplateDef] = []
    warnings: list[str] = []
    drafts: list[str] = []
    for node_id in nodes:
        template = select_template(templates, node_id, profile.task_level)
        if template is None:
            warnings.append(f"узел {node_id}: нет шаблона задания — узел пропущен")
            continue
        if template.status == "draft":
            drafts.append(template.id)
        if template.level != profile.task_level:
            warnings.append(
                f"узел {node_id}: нет шаблона уровня L{profile.task_level}, взят L{template.level}"
            )
        chosen.append(template)
    return SessionPlan(
        spec_code=spec_code,
        target_grade=target_grade,
        demo=demo,
        profile=profile,
        templates=tuple(chosen),
        warnings=tuple(warnings),
        draft_template_ids=tuple(drafts),
    )


def anchor_payload(template: TemplateDef) -> dict[str, Any]:
    """Снапшот эталонного варианта для `task_variant.payload` — самодостаточен для показа
    и грейдинга (ключи и рубрика наружу через API не отдаются)."""
    anchor = template.anchor_variant
    answer_key: Any = anchor.answer_key
    if answer_key is not None and not isinstance(answer_key, str):
        answer_key = answer_key.model_dump()
    return {
        "template_code": template.id,
        "kind": template.kind,
        "title": template.title,
        "statement": anchor.statement,
        "code": anchor.code,
        "options": (
            [{"id": o.key, "text": o.text} for o in anchor.options] if anchor.options else None
        ),
        "answer_key": answer_key,
        "reference_answer": anchor.reference_answer,
        "rubric": [c.model_dump() for c in template.rubric],
        "stack_tags": list(template.stack_tags),
        "time_limit_seconds": template.max_time_sec,
        "max_answer_chars": template.max_answer_chars,
        "template_status": template.status,
    }


def api_kind(payload: dict[str, Any]) -> str:
    """Вид задания в API: mcq_reasoned → choice (ответ choice_id), остальные → text."""
    return "choice" if payload.get("kind") == "mcq_reasoned" else "text"


def body_md(payload: dict[str, Any]) -> str:
    statement = str(payload.get("statement") or "")
    code = payload.get("code")
    if code:
        return f"{statement}\n\n```\n{str(code).rstrip()}\n```"
    return statement


def shuffled_options(payload: dict[str, Any], seed: str) -> list[dict[str, str]] | None:
    """Опции choice-задания в перемешанном (детерминированно по id задания) порядке."""
    options = payload.get("options")
    if not options:
        return None
    result = [dict(o) for o in options]
    random.Random(seed).shuffle(result)
    return result


# --- доступ к данным -----------------------------------------------------------------------


async def _load_items(db: AsyncSession, session_id: uuid.UUID) -> list[TestItem]:
    rows = (
        await db.execute(
            select(TestItem).where(TestItem.session_id == session_id).order_by(TestItem.position)
        )
    ).scalars()
    return list(rows.all())


async def _owner_profile(db: AsyncSession, user: AppUser) -> CandidateProfile:
    profile = await find_profile(db, user.id)
    if profile is None:
        raise conflict(
            "Заполните профиль кандидата (специализация обязательна) перед тестом",
            code="profile_incomplete",
        )
    return profile


async def _owned_session(
    db: AsyncSession, profile: CandidateProfile, session_id: uuid.UUID
) -> TestSession:
    row = (
        await db.execute(select(TestSession).where(TestSession.id == session_id))
    ).scalar_one_or_none()
    if row is None or row.candidate_id != profile.id:
        raise not_found("Сессия теста не найдена")
    return row


def current_index(items: list[TestItem]) -> int:
    return next((item.position for item in items if item.answered_at is None), len(items))


def cooldown_until(
    category: CandidateCategory | None, settings: Settings, now: datetime
) -> datetime | None:
    """Дата, раньше которой новый тест недоступен (None — доступен).

    Приоритет — сохранённая `grade_change_available_at`; иначе от `computed_at` +
    GRADE_COOLDOWN_DAYS (на стенде c TEST_DEMO_MODE — DEMO_COOLDOWN_SECONDS).
    """
    if category is None:
        return None
    until = category.grade_change_available_at
    if until is None:
        delta = (
            timedelta(seconds=settings.demo_cooldown_seconds)
            if settings.test_demo_mode
            else timedelta(days=settings.grade_cooldown_days)
        )
        until = category.computed_at + delta
    return until if until > now else None


# --- создание и чтение сессии ---------------------------------------------------------------


@dataclass(frozen=True)
class SessionView:
    session: TestSession
    items: list[TestItem]
    created: bool


async def create_session(
    db: AsyncSession,
    user: AppUser,
    target_grade: str,
    settings: Settings | None = None,
    *,
    mode: str | None = None,
) -> SessionView:
    """POST /assessment/sessions. Порядок запросов: профиль → активная сессия →
    [items активной] | [категория → шаблоны (одним IN-запросом)].

    `mode`: express — ограничения демо (≤5 заданий по демо-профилю), full — одно задание
    на каждый узел полного T-профиля грейда; None — старое поведение (TEST_DEMO_MODE env).
    """
    settings = settings or get_settings()
    demo = (mode == MODE_EXPRESS) if mode is not None else settings.test_demo_mode
    effective_mode = mode or (MODE_EXPRESS if settings.test_demo_mode else MODE_FULL)
    now = datetime.now(UTC)
    profile = await _owner_profile(db, user)
    if not profile.specialization_code:
        raise conflict(
            "В профиле не выбрана специализация — заполните профиль кандидата",
            code="profile_incomplete",
        )
    active = (
        await db.execute(
            select(TestSession).where(
                TestSession.candidate_id == profile.id, TestSession.status == "in_progress"
            )
        )
    ).scalar_one_or_none()
    if active is not None:
        return SessionView(session=active, items=await _load_items(db, active.id), created=False)

    category = (
        await db.execute(
            select(CandidateCategory).where(
                CandidateCategory.candidate_id == profile.id,
                CandidateCategory.specialization_code == profile.specialization_code,
            )
        )
    ).scalar_one_or_none()
    until = cooldown_until(category, settings, now)
    if until is not None:
        next_at = until.isoformat()
        raise ApiError(
            409,
            "cooldown_active",
            f"Повторный тест будет доступен {next_at}",
            details=[{"field": "next_attempt_at", "code": "cooldown", "message": next_at}],
        )

    plan = build_plan(
        profile.specialization_code,
        target_grade,
        demo=demo,
        templates=template_catalog(),
        skills=skills_catalog(),
    )
    if not plan.templates:
        raise conflict(
            "Для профиля грейда нет ни одного шаблона заданий — загрузите шаблоны (сид)",
            code="no_templates_available",
        )

    codes = [t.id for t in plan.templates]
    existing_rows = (
        await db.execute(select(TaskTemplateRow).where(TaskTemplateRow.code.in_(codes)))
    ).scalars()
    rows_by_code = {row.code: row for row in existing_rows.all()}
    for template in plan.templates:
        if template.id in rows_by_code:
            continue
        row = TaskTemplateRow(
            code=template.id,
            node_id=template.node,
            level=template.level,
            kind=template.kind,
            spec={
                "concept": template.concept,
                "invariant": template.invariant,
                "vary": template.vary,
                "forbidden_changes": template.forbidden_changes,
                "trap": template.trap,
            },
            rubric=[c.model_dump() for c in template.rubric],
            anchor_variant=template.anchor_variant.model_dump(),
            difficulty_anchor=template.difficulty_anchor.model_dump(),
            stack_tags=list(template.stack_tags),
            max_time_sec=template.max_time_sec,
            status=template.status,
        )
        db.add(row)
        rows_by_code[template.id] = row
    await db.flush()

    grades = ("junior", "middle", "senior")
    if category is None:
        kind = "initial"
    elif grades.index(target_grade) >= grades.index(category.grade):
        kind = "retest_up"
    else:
        kind = "retest_down"
    session_row = TestSession(
        candidate_id=profile.id,
        specialization_code=profile.specialization_code,
        target_grade=target_grade,
        mode="demo" if plan.demo else "full",
        kind=kind,
        status="in_progress",
        stage="base",
        seed=random.getrandbits(31),
        details={
            "demo": plan.demo,
            "mode": effective_mode,
            "draft_template_ids": list(plan.draft_template_ids),
            "warnings": list(plan.warnings),
        },
        started_at=now,
        expires_at=now + timedelta(hours=SESSION_TTL_HOURS),
    )
    db.add(session_row)
    await db.flush()

    variants: list[TaskVariant] = []
    for position, template in enumerate(plan.templates):
        payload = anchor_payload(template)
        digest = hashlib.sha256(f"{template.id}:{session_row.id}:{position}".encode()).hexdigest()
        variant = TaskVariant(
            template_id=rows_by_code[template.id].id,
            payload=payload,
            content_hash=digest,
            uses_count=1,
            max_uses=1,
            status="retired",
            retired_reason="session_anchor",
            personal_for_candidate_id=profile.id,
        )
        db.add(variant)
        variants.append(variant)
    await db.flush()

    items: list[TestItem] = []
    for position, (template, variant) in enumerate(zip(plan.templates, variants, strict=True)):
        item = TestItem(
            session_id=session_row.id,
            candidate_id=profile.id,
            variant_id=variant.id,
            node_id=template.node,
            level=template.level,
            position=position,
            served_at=now,
            deadline_at=now + timedelta(seconds=template.max_time_sec),
        )
        db.add(item)
        items.append(item)
    await db.flush()
    await db.commit()
    return SessionView(session=session_row, items=items, created=True)


async def get_current_session(db: AsyncSession, user: AppUser) -> SessionView:
    """GET /assessment/sessions/current. Запросы: профиль → активная сессия → items."""
    profile = await find_profile(db, user.id)
    if profile is None:
        raise not_found("Активной сессии теста нет")
    active = (
        await db.execute(
            select(TestSession).where(
                TestSession.candidate_id == profile.id, TestSession.status == "in_progress"
            )
        )
    ).scalar_one_or_none()
    if active is None:
        raise not_found("Активной сессии теста нет")
    return SessionView(session=active, items=await _load_items(db, active.id), created=False)


@dataclass(frozen=True)
class CurrentItem:
    item: TestItem
    payload: dict[str, Any]
    index: int
    total: int


async def get_current_item(db: AsyncSession, user: AppUser, session_id: uuid.UUID) -> CurrentItem:
    """GET .../items/current. Запросы: профиль → сессия → items → вариант.

    Первый показ задания запускает серверный таймер (deadline_at от текущего момента);
    в демо-режиме таймер мягкий — просрочка только помечает ответ флагом `late`.
    """
    profile = await find_profile(db, user.id)
    if profile is None:
        raise not_found("Сессия теста не найдена")
    session_row = await _owned_session(db, profile, session_id)
    if session_row.status != "in_progress":
        raise not_found("Активного задания нет: сессия завершена")
    items = await _load_items(db, session_id)
    item = next((it for it in items if it.answered_at is None), None)
    if item is None:
        raise not_found("Все задания отвечены — завершите сессию (finish)")
    variant = (
        await db.execute(select(TaskVariant).where(TaskVariant.id == item.variant_id))
    ).scalar_one_or_none()
    payload: dict[str, Any] = dict(variant.payload or {}) if variant is not None else {}
    if SERVED_FLAG not in (item.flags or []):
        now = datetime.now(UTC)
        item.served_at = now
        limit = int(payload.get("time_limit_seconds") or 300)
        item.deadline_at = now + timedelta(seconds=limit)
        item.flags = [*(item.flags or []), SERVED_FLAG]
        await db.commit()
    return CurrentItem(item=item, payload=payload, index=item.position, total=len(items))


@dataclass(frozen=True)
class AnswerResult:
    finished: bool
    next_index: int | None


async def submit_answer(
    db: AsyncSession,
    user: AppUser,
    session_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    text: str | None,
    choice_id: str | None,
) -> AnswerResult:
    """POST .../items/{item_id}/answer. Запросы: профиль → сессия → items → вариант.

    Ответ сохраняется (оценка — на finish); повторный сабмит того же задания → 409
    `item_already_answered`; просроченный дедлайн не блокирует, а помечает флагом."""
    profile = await find_profile(db, user.id)
    if profile is None:
        raise not_found("Сессия теста не найдена")
    session_row = await _owned_session(db, profile, session_id)
    if session_row.status != "in_progress":
        raise conflict("Сессия уже завершена", code="session_not_active")
    items = await _load_items(db, session_id)
    item = next((it for it in items if it.id == item_id), None)
    if item is None:
        raise not_found("Задание не найдено в этой сессии")
    if item.answered_at is not None:
        raise conflict("Ответ на это задание уже принят", code="item_already_answered")
    variant = (
        await db.execute(select(TaskVariant).where(TaskVariant.id == item.variant_id))
    ).scalar_one_or_none()
    payload: dict[str, Any] = dict(variant.payload or {}) if variant is not None else {}
    kind = api_kind(payload)
    if kind == "choice":
        option_ids = {str(o["id"]) for o in (payload.get("options") or [])}
        if not choice_id or (option_ids and choice_id not in option_ids):
            raise validation_error(
                "Для этого задания нужен choice_id из списка опций",
                [{"field": "choice_id", "code": "required", "message": "выберите опцию"}],
            )
        item.answer = {"choice_id": choice_id}
    else:
        if not text or not text.strip():
            raise validation_error(
                "Для этого задания нужен текстовый ответ",
                [{"field": "text", "code": "required", "message": "введите ответ"}],
            )
        max_chars = int(payload.get("max_answer_chars") or 3000)
        item.answer = {"text": text.strip()[:max_chars]}
    now = datetime.now(UTC)
    item.answered_at = now
    if item.deadline_at is not None and now > item.deadline_at:
        item.flags = [*(item.flags or []), LATE_FLAG]  # мягкий таймер (демо)
    next_position = next(
        (it.position for it in items if it.answered_at is None and it.id != item.id), None
    )
    await db.commit()
    return AnswerResult(finished=next_position is None, next_index=next_position)


# --- грейдинг (finish) -----------------------------------------------------------------------


def score_choice(payload: dict[str, Any], choice_id: str | None) -> float:
    """Закрытое задание — детерминированно по ключу шаблона."""
    answer_key = payload.get("answer_key")
    return 1.0 if choice_id is not None and str(answer_key) == str(choice_id) else 0.0


async def grade_text_answer(
    llm: LLMService, payload: dict[str, Any], answer_text: str
) -> tuple[Grading, list[str]]:
    """Открытый ответ: answer_grade через LLM-сервис; при любой ошибке — эвристика rules
    (`grade_by_keywords`), сессия не падает. Балл пересчитывается по весам рубрики."""
    rubric = [dict(c) for c in (payload.get("rubric") or [])]
    variant = {
        "statement": payload.get("statement") or "",
        "reference_answer": payload.get("reference_answer") or "",
    }
    try:
        result = await llm.complete(
            "answer_grade",
            {
                "variant": json.dumps(variant, ensure_ascii=False),
                "rubric": json.dumps(rubric, ensure_ascii=False),
                "answer": answer_text,
            },
            schema=Grading,
            session=None,
        )
        parsed = result.parsed
        if not isinstance(parsed, Grading):  # устаревший кэш другой схемы
            raise TypeError(f"answer_grade вернул {type(parsed).__name__}")
        grading = parsed
        flags: list[str] = []
    except Exception as exc:  # noqa: BLE001 — грейдинг не должен ронять finish
        logger.warning("answer_grade: ошибка LLM, эвристика rules: %s", exc)
        grading = grade_by_keywords(answer_text, rubric, str(variant["reference_answer"]))
        flags = [LLM_FALLBACK_FLAG]
    grading.score = grading.weighted_score(rubric)
    return grading, flags


def node_scores_of(items: list[TestItem]) -> dict[str, float]:
    """Балл узла — среднее по заданиям узла (неотвеченные считаются нулём)."""
    by_node: dict[str, list[float]] = {}
    for item in items:
        by_node.setdefault(item.node_id, []).append(float(item.score or 0.0))
    return {node: round(sum(vals) / len(vals), 4) for node, vals in by_node.items()}


def api_node_state(active: bool, weight_now: float, fresh: float, dim: float) -> str:
    """Состояние узла по контракту: fresh | fading | expired | off."""
    if not active:
        return "off"
    if weight_now >= fresh:
        return "fresh"
    if weight_now >= dim:
        return "fading"
    return "expired"


@dataclass(frozen=True)
class FinishResult:
    session: TestSession
    grade: str | None
    grade_confirmed: bool
    q_level: int
    # [{code, title, score, state}]
    nodes: list[dict[str, Any]]
    integrity: dict[str, Any] | None


async def _reconstructed_nodes(db: AsyncSession, session_row: TestSession) -> list[dict[str, Any]]:
    """Блок nodes для сессии, завершённой до появления сохранённого результата:
    баллы — из `session_row.node_scores`, состав узлов — из профиля грейда, состояние —
    по текущему снимку `candidate_skill_state` (нет строки — state="off", вес 0).

    Запросы: снимки узлов (IN по узлам профиля)."""
    skills = skills_catalog()
    spec = skills.spec(session_row.specialization_code)
    if spec is None:
        raise not_found("Специализация сессии не найдена в справочнике")
    grade_profile = skills.profile(session_row.specialization_code, session_row.target_grade)
    if grade_profile is None:
        raise not_found("Профиль грейда не найден в справочнике")
    demo = bool((session_row.details or {}).get("demo"))
    requirements = (grade_profile.demo if demo else grade_profile).requirements()
    target_nodes = list(requirements.target_nodes)
    thresholds = spec.file.thresholds
    scores = dict(session_row.node_scores or {})
    state_rows = (
        await db.execute(
            select(CandidateSkillState).where(
                CandidateSkillState.candidate_id == session_row.candidate_id,
                CandidateSkillState.node_id.in_(target_nodes),
            )
        )
    ).scalars()
    states_by_node = {row.node_id: row for row in state_rows.all()}
    nodes_out: list[dict[str, Any]] = []
    for node_id in target_nodes:
        node_def = spec.nodes_by_id.get(node_id)
        row = states_by_node.get(node_id)
        active = row is not None and int(row.active_level or 0) >= 1
        weight_now = float(row.w_now or 0.0) if row is not None else 0.0
        nodes_out.append(
            {
                "code": node_id,
                "title": node_def.title if node_def is not None else node_id,
                "score": float(scores.get(node_id, 0.0)),
                "state": api_node_state(active, weight_now, thresholds.fresh, thresholds.dim),
            }
        )
    return nodes_out


async def _finished_result(db: AsyncSession, session_row: TestSession) -> FinishResult:
    """Итог уже завершённой сессии (идемпотентный повторный finish, НЕ 409).

    Сначала — сохранённый `details["result"]`; у старых сессий (завершены до этого
    релиза) его нет — реконструкция. Запросы: [result сохранён — запросов нет] |
    [категория → снимки узлов → события телеметрии [→ строка кэша целостности]]."""
    saved = (session_row.details or {}).get("result")
    if isinstance(saved, dict) and "nodes" in saved:
        return FinishResult(
            session=session_row,
            grade=saved.get("grade"),
            grade_confirmed=bool(saved.get("grade_confirmed")),
            q_level=int(saved.get("q_level") or 0),
            nodes=list(saved.get("nodes") or []),
            integrity=saved.get("integrity"),
        )
    grade_confirmed = session_row.outcome in ("passed", "strong")
    category = (
        await db.execute(
            select(CandidateCategory).where(
                CandidateCategory.candidate_id == session_row.candidate_id,
                CandidateCategory.specialization_code == session_row.specialization_code,
            )
        )
    ).scalar_one_or_none()
    nodes_out = await _reconstructed_nodes(db, session_row)
    integrity_result, _ = await recompute_session_integrity(
        db, session_row, get_integrity_settings()
    )
    integrity_out = (
        None
        if integrity_result.events_total == 0
        else {
            "level": integrity_result.level,
            "flags": [flag.as_dict() for flag in integrity_result.flags],
        }
    )
    return FinishResult(
        session=session_row,
        grade=session_row.target_grade if grade_confirmed else None,
        grade_confirmed=grade_confirmed,
        q_level=int(category.q_level or 0) if category is not None else 0,
        nodes=nodes_out,
        integrity=integrity_out,
    )


async def finish_session(
    db: AsyncSession, user: AppUser, session_id: uuid.UUID, settings: Settings | None = None
) -> FinishResult:
    """POST .../finish. Запросы: профиль → сессия → [завершённая: _finished_result] |
    [items → варианты (IN) → commit(stage=grading) → LLM-грейдинг → подтверждения
    кандидата по узлам → candidate_skill_state → категория → события телеметрии
    [→ строка кэша целостности]].

    Идемпотентность: finish по уже завершённой сессии возвращает сохранённый итог
    (НЕ 409) — ретрай фронта после таймаута прокси получает тот же результат.
    Гонка: stage=grading фиксируется commit'ом ДО LLM-цикла; параллельный finish
    видит его и отвечает 409 `grading_in_progress` (фронт ретраит по этому коду)."""
    settings = settings or get_settings()
    now = datetime.now(UTC)
    profile = await find_profile(db, user.id)
    if profile is None:
        raise not_found("Сессия теста не найдена")
    session_row = await _owned_session(db, profile, session_id)
    if session_row.status == "finished":
        return await _finished_result(db, session_row)
    if session_row.status != "in_progress":
        raise conflict("Сессия уже завершена", code="session_not_active")
    if session_row.stage == STAGE_GRADING:
        raise conflict("Оценка уже выполняется", code="grading_in_progress")
    # фиксация этапа до медленного LLM-цикла; фабрика сессий (core/db.py) создана
    # с expire_on_commit=False — ORM-объекты после commit не истекают, перечитка не нужна
    session_row.stage = STAGE_GRADING
    await db.commit()
    items = await _load_items(db, session_id)
    variant_rows = (
        await db.execute(
            select(TaskVariant).where(TaskVariant.id.in_([it.variant_id for it in items]))
        )
    ).scalars()
    payloads = {row.id: dict(row.payload or {}) for row in variant_rows.all()}

    llm = get_llm_service()
    for item in items:
        payload = payloads.get(item.variant_id, {})
        answer = item.answer or {}
        if item.answered_at is None:
            item.score = Decimal("0")
            item.grading = {"kind": "skipped"}
            item.flags = [*(item.flags or []), UNANSWERED_FLAG]
        elif api_kind(payload) == "choice":
            value = score_choice(payload, answer.get("choice_id"))
            item.score = Decimal(str(round(value, 4)))
            item.grading = {"kind": "choice", "correct": value >= 1.0}
        else:
            # последовательный грейдинг: конкурентность 1 (лимит стенда GigaChat)
            grading, flags = await grade_text_answer(llm, payload, str(answer.get("text") or ""))
            item.score = Decimal(str(round(grading.score, 4)))
            item.grading = {"kind": "text", **grading.model_dump(mode="json")}
            if flags:
                item.flags = [*(item.flags or []), *flags]

    scores = node_scores_of(items)
    skills = skills_catalog()
    spec: SpecGraph | None = skills.spec(session_row.specialization_code)
    if spec is None:
        raise not_found("Специализация сессии не найдена в справочнике")
    grade_profile = skills.profile(session_row.specialization_code, session_row.target_grade)
    if grade_profile is None:
        raise not_found("Профиль грейда не найден в справочнике")
    demo = bool((session_row.details or {}).get("demo"))
    weights = spec.file.weights
    decay = spec.file.decay
    thresholds = spec.file.thresholds

    new_confirmations: list[SkillConfirmation] = []
    item_levels: dict[str, int] = {}
    for item in items:
        item_levels[item.node_id] = max(item_levels.get(item.node_id, 1), int(item.level))
    for node_id, score in scores.items():
        w0 = w0_for_score(score, weights.steps("test"))
        if w0 is None:
            continue  # балл < 0.5: остаётся только в сессии (PLAN.md §3.3)
        if demo:
            w0 *= weights.demo_multiplier
        half_life = half_life_for(
            "test", spec.layer_of(node_id), decay.half_life_days, decay.layer_factor
        )
        new_confirmations.append(
            SkillConfirmation(
                candidate_id=profile.id,
                node_id=node_id,
                level=item_levels.get(node_id, 1),
                w0=Decimal(str(round(w0, 4))),
                source="test",
                source_ref=session_row.id,
                confirmed_at=now,
                half_life_days=max(1, int(round(half_life))),
            )
        )
        db.add(new_confirmations[-1])

    # активность узла для грейда — только по подтверждениям ЭТОЙ сессии (PLAN.md §3.4)
    session_conf_by_node: dict[str, list[Confirmation]] = {}
    for row in new_confirmations:
        session_conf_by_node.setdefault(row.node_id, []).append(Confirmation.from_row(row))
    active_nodes = {
        node_id
        for node_id, confs in session_conf_by_node.items()
        if node_state(node_id, confs, now, thresholds.active).active_level >= 1
    }

    # материализованный снимок — по всем подтверждениям затронутых узлов (старым и новым);
    # новые строки могли попасть в выборку через autoflush — исключаем по identity
    affected = sorted(scores)
    existing_conf_rows = (
        await db.execute(
            select(SkillConfirmation).where(
                SkillConfirmation.candidate_id == profile.id,
                SkillConfirmation.node_id.in_(affected),
            )
        )
    ).scalars()
    new_ids = {id(row) for row in new_confirmations}
    all_confirmations = [
        Confirmation.from_row(r) for r in existing_conf_rows.all() if id(r) not in new_ids
    ] + [Confirmation.from_row(r) for r in new_confirmations]
    state_rows = (
        await db.execute(
            select(CandidateSkillState).where(
                CandidateSkillState.candidate_id == profile.id,
                CandidateSkillState.node_id.in_(affected),
            )
        )
    ).scalars()
    states_by_node = {row.node_id: row for row in state_rows.all()}
    for node_id in affected:
        snapshot = node_state(
            node_id, all_confirmations, now, thresholds.active, thresholds.fresh, thresholds.dim
        )
        row = states_by_node.get(node_id)
        if row is None:
            row = CandidateSkillState(candidate_id=profile.id, node_id=node_id)
            db.add(row)
            states_by_node[node_id] = row
        row.active_level = snapshot.active_level
        row.strength_by_level = snapshot.strength_by_level_json
        row.w_now = Decimal(str(round(snapshot.w_now, 4)))
        row.freshest_at = snapshot.freshest_at
        row.computed_at = now

    requirements = (grade_profile.demo if demo else grade_profile).requirements()
    check = check_grade_profile(requirements, active_nodes, spec.adjacency, spec.layer_of)
    confident = check.passed and is_confident_pass(requirements, active_nodes, scores)
    outcome = "strong" if confident else ("passed" if check.passed else "failed")

    category = (
        await db.execute(
            select(CandidateCategory).where(
                CandidateCategory.candidate_id == profile.id,
                CandidateCategory.specialization_code == session_row.specialization_code,
            )
        )
    ).scalar_one_or_none()
    previous_grade = category.grade if category is not None else None
    cooldown = (
        timedelta(seconds=settings.demo_cooldown_seconds)
        if settings.test_demo_mode
        else timedelta(days=settings.grade_cooldown_days)
    )
    if check.passed:
        shape = profile_shape(
            active_nodes, session_row.specialization_code, skills.bridge_adjacency
        )
        if category is None:
            category = CandidateCategory(
                candidate_id=profile.id,
                specialization_code=session_row.specialization_code,
                history=[],
            )
            db.add(category)
        category.grade = session_row.target_grade
        category.q_level = check.q
        category.shape = shape.shape if shape.shape != "none" else "dot"
        category.source_session_id = session_row.id
        category.computed_at = now
        category.grade_change_available_at = now + cooldown
        category.upgrade_window_until = now + timedelta(hours=UPGRADE_WINDOW_HOURS)
        category.history = [
            *(category.history or []),
            {
                "session_id": str(session_row.id),
                "mode": session_row.mode,
                "target_grade": session_row.target_grade,
                "outcome": outcome,
                "at": now.isoformat(),
            },
        ]
        # видимость в банке: privacy.searchable И категория есть (PLAN.md §3.7)
        profile.searchable = bool((profile.privacy or {}).get("searchable", True))
    db.add(
        GradeHistory(
            candidate_id=profile.id,
            specialization_code=session_row.specialization_code,
            session_id=session_row.id,
            from_grade=previous_grade,
            to_grade=session_row.target_grade if check.passed else previous_grade,
            outcome=outcome,
            reason="demo_session" if demo else "test_session",
        )
    )

    # подтверждённый стек: теги шаблонов, на которые кандидат ответил на балл ≥ 0.7
    confirmed_tags = set(profile.stack_tags_confirmed or [])
    for item in items:
        if float(item.score or 0.0) >= CONFIRMED_SCORE:
            confirmed_tags.update(payloads.get(item.variant_id, {}).get("stack_tags") or [])
    profile.stack_tags_confirmed = sorted(confirmed_tags)

    session_row.status = "finished"
    session_row.stage = "done"
    session_row.outcome = outcome
    session_row.node_scores = dict(scores)
    session_row.finished_at = now

    integrity_result, _ = await recompute_session_integrity(
        db, session_row, get_integrity_settings()
    )
    integrity_out = (
        None
        if integrity_result.events_total == 0
        else {
            "level": integrity_result.level,
            "flags": [flag.as_dict() for flag in integrity_result.flags],
        }
    )

    target_nodes = list(requirements.target_nodes)
    nodes_out: list[dict[str, Any]] = []
    for node_id in target_nodes:
        active = node_id in active_nodes
        weight_now = max(
            (float(c.w0) for c in new_confirmations if c.node_id == node_id), default=0.0
        )
        node_def = spec.nodes_by_id.get(node_id)
        nodes_out.append(
            {
                "code": node_id,
                "title": node_def.title if node_def is not None else node_id,
                "score": scores.get(node_id, 0.0),
                "state": api_node_state(active, weight_now, thresholds.fresh, thresholds.dim),
            }
        )
    grade = session_row.target_grade if check.passed else previous_grade
    # полный итог — в details сессии: повторный finish вернёт его без пересчёта
    # (в JSONB только float/int/str, Decimal сюда не попадает)
    session_row.details = {
        **(session_row.details or {}),
        "result": {
            "grade": grade,
            "grade_confirmed": check.passed,
            "q_level": check.q,
            "nodes": nodes_out,
            "integrity": integrity_out,
        },
    }
    await db.commit()
    return FinishResult(
        session=session_row,
        grade=grade,
        grade_confirmed=check.passed,
        q_level=check.q,
        nodes=nodes_out,
        integrity=integrity_out,
    )


# --- разбор последней завершённой сессии (review) --------------------------------------------


@dataclass(frozen=True)
class ReviewData:
    """Данные разбора: nodes — [{code, title, score, state}], items — по контракту
    ReviewItemOut (эталонов и ключей choice здесь нет — банк заданий закрытый)."""

    session: TestSession
    nodes: list[dict[str, Any]]
    items: list[dict[str, Any]]


REVIEW_ITEM_KINDS = ("choice", "text", "skipped")


async def get_last_review(db: AsyncSession, user: AppUser) -> ReviewData:
    """GET /assessment/sessions/last/review — где кандидат ошибся и что улучшить.

    Наружу уходят только тексты критериев рубрики, попадания (hit), цитаты-подтверждения
    и короткая обратная связь; эталонные ответы и ключи choice не отдаются.

    Запросы: профиль → последняя завершённая сессия → items → варианты (IN)
    [→ при отсутствии сохранённого result — снимки узлов (реконструкция)]."""
    profile = await find_profile(db, user.id)
    if profile is None:
        raise not_found("Завершённых сессий нет")
    session_row = (
        await db.execute(
            select(TestSession)
            .where(TestSession.candidate_id == profile.id, TestSession.status == "finished")
            .order_by(TestSession.finished_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if session_row is None:
        raise not_found("Завершённых сессий нет")
    items = await _load_items(db, session_row.id)
    variant_rows = (
        await db.execute(
            select(TaskVariant).where(TaskVariant.id.in_([it.variant_id for it in items]))
        )
    ).scalars()
    payloads = {row.id: dict(row.payload or {}) for row in variant_rows.all()}
    spec = skills_catalog().spec(session_row.specialization_code)
    nodes_by_id = spec.nodes_by_id if spec is not None else {}

    items_out: list[dict[str, Any]] = []
    for item in items:
        payload = payloads.get(item.variant_id, {})
        grading = dict(item.grading or {})
        kind = grading.get("kind")
        if kind not in REVIEW_ITEM_KINDS:  # сессии, завершённые без грейдинга item'а
            kind = "skipped" if item.answered_at is None else api_kind(payload)
        # тексты критериев — join по id с рубрикой из снапшота варианта
        rubric_text = {
            str(c.get("id")): str(c.get("text") or "") for c in (payload.get("rubric") or [])
        }
        criteria = [
            {
                "id": str(c.get("id")),
                "text": rubric_text.get(str(c.get("id")), ""),
                "hit": float(c.get("hit") or 0.0),
                "evidence": str(c.get("evidence") or ""),
            }
            for c in (grading.get("criteria") or [])
        ]
        node_def = nodes_by_id.get(item.node_id)
        items_out.append(
            {
                "node_code": item.node_id,
                "node_title": node_def.title if node_def is not None else item.node_id,
                "title": str(payload.get("title") or item.node_id),
                "kind": kind,
                "score": float(item.score or 0.0),
                "answered": item.answered_at is not None,
                "correct": (
                    bool(grading["correct"]) if kind == "choice" and "correct" in grading else None
                ),
                "criteria": criteria,
                "feedback_short": str(grading.get("feedback_short") or ""),
                "llm_fallback": LLM_FALLBACK_FLAG in (item.flags or []),
            }
        )

    saved = (session_row.details or {}).get("result") or {}
    nodes = list(saved.get("nodes") or []) or await _reconstructed_nodes(db, session_row)
    return ReviewData(session=session_row, nodes=nodes, items=items_out)
