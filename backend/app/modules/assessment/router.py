"""Роутер модуля `assessment` (PLAN.md §7.5).

Каталог шаблонов заданий — `GET /assessment/templates?spec=&node=&level=`,
метаданные без ключей и эталонных ответов. Доступ двумя точками входа с одним обработчиком:
- `GET /assessment/templates` — пользователь с ролью `admin` (JWT Keycloak);
- `GET /assessment/internal/templates` — инструменты и джобы по `X-Internal-Token`.
Две точки вместо одной «или»-зависимости сохраняют стандартные `require_roles` /
`require_internal_token` и подмену `as_user` в тестах.

Сигналы целостности тестовой сессии (контракт интегратора, зона раннера не затрагивается):
- `POST /assessment/sessions/{id}/events` — батч телеметрии от раннера (только кандидат-
  владелец сессии; в payload — только длины и тайминги, текстов ответов нет);
- `GET /assessment/sessions/{id}/integrity` — агрегат high/medium/low (владелец или admin;
  просмотр админом пишется в аудит).

Сессии теста (контракт интегратора 11.10, минимальный core пути кандидата):
- `POST /assessment/sessions` — создать сессию (201) или вернуть уже активную (200);
  409 `profile_incomplete` без специализации в профиле, 409 `cooldown_active` по кулдауну;
- `GET /assessment/sessions/current` — активная сессия или 404;
- `GET /assessment/sessions/{id}/items/current` — текущее задание без эталонов и рубрик;
- `POST /assessment/sessions/{id}/items/{item_id}/answer` — сохранить ответ
  (повторно — 409 `item_already_answered`); оценка выполняется на finish;
- `POST /assessment/sessions/{id}/finish` — грейдинг, подтверждения, категория, итог;
  идемпотентен: по завершённой сессии возвращает сохранённый итог (ретрай после таймаута
  прокси), при идущем грейдинге — 409 `grading_in_progress` (фронт ретраит по коду);
- `GET /assessment/sessions/last/review` — разбор последней завершённой сессии кандидата:
  баллы, критерии рубрики с попаданиями и обратная связь (без эталонов и ключей choice).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import client_ip, write_audit
from app.core.db import get_session
from app.core.errors import ApiError, not_found
from app.core.security import (
    ROLE_ADMIN,
    ROLE_CANDIDATE,
    require_internal_token,
    require_roles,
)
from app.models.assessment import TestSession, TestSessionEvent
from app.models.candidate import CandidateProfile
from app.models.user import AppUser
from app.modules.assessment import sessions as sessions_service
from app.modules.assessment.config import get_integrity_settings
from app.modules.assessment.integrity import recompute_session_integrity
from app.modules.assessment.schemas import (
    AnswerIn,
    AnswerOut,
    EventsAcceptedOut,
    EventsBatchIn,
    FinishIntegrityOut,
    FinishNodeOut,
    FinishOut,
    IntegrityFlagOut,
    IntegrityOut,
    ItemBodyOut,
    ItemCurrentOut,
    ItemOptionOut,
    ReviewCriterionOut,
    ReviewItemOut,
    SessionCreateIn,
    SessionOut,
    SessionReviewOut,
    TemplateListResponse,
    TemplateMeta,
)
from app.modules.assessment.templates import TemplateLoadError, get_catalog, list_templates

router = APIRouter(prefix="/assessment", tags=["assessment"])

_internal = APIRouter(prefix="/internal", dependencies=[Depends(require_internal_token)])

SpecQuery = Annotated[
    str | None, Query(pattern=r"^[a-z]{2,16}$", description="Код специализации: be, fe, …")
]
NodeQuery = Annotated[
    str | None, Query(pattern=r"^[a-z]{2,16}\.[DBC]\d$", description="Узел, например be.B1")
]
LevelQuery = Annotated[int | None, Query(ge=1, le=3, description="Уровень задания 1..3")]
KindQuery = Annotated[
    Literal["mcq_reasoned", "code_review", "short_design", "scenario_personal"] | None,
    Query(description="Вид задания"),
]
StatusQuery = Annotated[Literal["draft", "reviewed"] | None, Query(description="Статус вычитки")]


def _list(
    spec: str | None, node: str | None, level: int | None, kind: str | None, status: str | None
) -> TemplateListResponse:
    try:
        catalog = get_catalog()
    except TemplateLoadError as exc:
        raise ApiError(
            500,
            "templates_invalid",
            "Каталог шаблонов заданий не загружен: ошибки формата в YAML",
            details=[{"path": p.path, "message": p.message} for p in exc.problems],
        ) from exc
    templates = list_templates(spec, node, level, kind=kind, status=status, catalog=catalog)
    return TemplateListResponse(
        items=[TemplateMeta.from_template(t) for t in templates],
        total=len(templates),
        warnings=list(catalog.warnings),
    )


@router.get(
    "/templates",
    response_model=TemplateListResponse,
    summary="Каталог шаблонов заданий (метаданные, без ответов) — для admin",
    dependencies=[Depends(require_roles(ROLE_ADMIN))],
)
async def list_task_templates(
    spec: SpecQuery = None,
    node: NodeQuery = None,
    level: LevelQuery = None,
    kind: KindQuery = None,
    status: StatusQuery = None,
) -> TemplateListResponse:
    return _list(spec, node, level, kind, status)


@_internal.get(
    "/templates",
    response_model=TemplateListResponse,
    summary="Каталог шаблонов заданий (метаданные, без ответов) — по X-Internal-Token",
)
async def list_task_templates_internal(
    spec: SpecQuery = None,
    node: NodeQuery = None,
    level: LevelQuery = None,
    kind: KindQuery = None,
    status: StatusQuery = None,
) -> TemplateListResponse:
    return _list(spec, node, level, kind, status)


# --- сигналы целостности (контракт раннера теста) ----------------------------------------

Session = Annotated[AsyncSession, Depends(get_session)]
CandidateUser = Annotated[AppUser, Depends(require_roles(ROLE_CANDIDATE))]
IntegrityViewer = Annotated[AppUser, Depends(require_roles(ROLE_CANDIDATE, ROLE_ADMIN))]


async def _get_test_session(session: Session, session_id: UUID) -> TestSession:
    test_session = (
        await session.execute(select(TestSession).where(TestSession.id == session_id))
    ).scalar_one_or_none()
    if test_session is None:
        raise not_found("Сессия теста не найдена")
    return test_session


async def _get_owner_profile(session: Session, user: AppUser) -> CandidateProfile | None:
    return (
        await session.execute(select(CandidateProfile).where(CandidateProfile.user_id == user.id))
    ).scalar_one_or_none()


@router.post(
    "/sessions/{session_id}/events",
    response_model=EventsAcceptedOut,
    status_code=202,
    summary="Батч телеметрии раннера (только длины и тайминги, текстов ответов нет)",
    responses={413: {"description": "too_many_events — в батче больше лимита событий"}},
)
async def post_session_events(
    session_id: UUID, payload: EventsBatchIn, user: CandidateUser, session: Session
) -> EventsAcceptedOut:
    """Приём событий кандидатом-владельцем сессии; чужая сессия не раскрывается (404).
    Порядок запросов: сессия → профиль → [вставка] → события → строка кэша."""
    settings = get_integrity_settings()
    if len(payload.events) > settings.integrity_max_batch_events:
        raise ApiError(
            413,
            "too_many_events",
            f"В одном батче принимается не больше {settings.integrity_max_batch_events} "
            f"событий, получено {len(payload.events)}",
        )
    test_session = await _get_test_session(session, session_id)
    profile = await _get_owner_profile(session, user)
    if profile is None or test_session.candidate_id != profile.id:
        raise not_found("Сессия теста не найдена")
    added: list[TestSessionEvent] = []
    for event in payload.events:
        client_ts = event.client_ts
        if client_ts.tzinfo is None:
            client_ts = client_ts.replace(tzinfo=UTC)
        row = TestSessionEvent(
            session_id=test_session.id,
            kind=event.kind,
            client_ts=client_ts,
            payload=event.payload.model_dump(exclude_none=True),
        )
        session.add(row)
        added.append(row)
    await session.flush()
    # агрегат пересчитывается сразу: кэш всегда актуален для выдачи работодателя
    await recompute_session_integrity(session, test_session, settings, extra_events=added)
    await session.commit()
    return EventsAcceptedOut(accepted=len(added))


@router.get(
    "/sessions/{session_id}/integrity",
    response_model=IntegrityOut,
    summary="Агрегат целостности сессии (владелец-кандидат или admin; формула честная)",
)
async def get_session_integrity(
    session_id: UUID, user: IntegrityViewer, session: Session, request: Request
) -> IntegrityOut:
    """Порядок запросов: сессия → [профиль, если роль candidate] → события → строка кэша.
    Просмотр админом (не владельцем) пишется в аудит."""
    test_session = await _get_test_session(session, session_id)
    is_owner = False
    if ROLE_CANDIDATE in user.effective_roles:
        profile = await _get_owner_profile(session, user)
        is_owner = profile is not None and test_session.candidate_id == profile.id
    is_admin = ROLE_ADMIN in user.effective_roles
    if not is_owner and not is_admin:
        raise not_found("Сессия теста не найдена")
    result, row = await recompute_session_integrity(session, test_session, get_integrity_settings())
    if is_admin and not is_owner:
        await write_audit(
            session,
            actor_user_id=user.id,
            action="integrity_viewed",
            entity_type="test_session",
            entity_id=test_session.id,
            after={"level": result.level, "events_total": result.events_total},
            ip=client_ip(request),
        )
    await session.commit()
    return IntegrityOut(
        level=result.level,  # type: ignore[arg-type]
        flags=[IntegrityFlagOut(**flag.as_dict()) for flag in result.flags],
        events_total=result.events_total,
        computed_at=row.computed_at if row is not None else datetime.now(UTC),
    )


# --- сессии теста (контракт интегратора 11.10) --------------------------------------------


def _session_out(view: sessions_service.SessionView) -> SessionOut:
    mode = (view.session.details or {}).get("mode")
    return SessionOut(
        id=view.session.id,
        status=sessions_service.API_STATUS_ACTIVE,
        specialization=view.session.specialization_code,
        target_grade=view.session.target_grade,
        mode=mode if mode in ("express", "full") else None,
        items_total=len(view.items),
        current_index=sessions_service.current_index(view.items),
    )


@router.post(
    "/sessions",
    response_model=SessionOut,
    status_code=201,
    summary="Создать сессию теста на грейд (активная сессия возвращается как есть, 200)",
    responses={
        409: {"description": "profile_incomplete | cooldown_active | no_templates_available"}
    },
)
async def create_test_session(
    payload: SessionCreateIn, user: CandidateUser, session: Session, response: Response
) -> SessionOut:
    """`mode`: express — демо-ограничения (≤5 заданий), full — все узлы профиля грейда;
    отсутствует — старое поведение по TEST_DEMO_MODE."""
    view = await sessions_service.create_session(
        session, user, payload.target_grade, mode=payload.mode
    )
    if not view.created:
        response.status_code = 200
    return _session_out(view)


@router.get(
    "/sessions/current",
    response_model=SessionOut,
    summary="Активная сессия теста (404 — активной сессии нет)",
)
async def get_current_test_session(user: CandidateUser, session: Session) -> SessionOut:
    return _session_out(await sessions_service.get_current_session(session, user))


# роут с литеральным «last» объявлен ДО роутов с {session_id},
# чтобы сегмент «last» не пытался разбираться как UUID
@router.get(
    "/sessions/last/review",
    response_model=SessionReviewOut,
    summary="Разбор последней завершённой сессии: критерии, попадания, обратная связь",
)
async def get_last_session_review(user: CandidateUser, session: Session) -> SessionReviewOut:
    """Только кандидат-владелец. Эталонные ответы и ключи choice не отдаются —
    банк заданий закрытый; видны тексты критериев рубрики, hit и feedback_short."""
    review = await sessions_service.get_last_review(session, user)
    mode = (review.session.details or {}).get("mode")
    return SessionReviewOut(
        session_id=review.session.id,
        finished_at=review.session.finished_at,
        mode=mode if mode in ("express", "full") else None,
        target_grade=review.session.target_grade,
        outcome=review.session.outcome,
        nodes=[FinishNodeOut(**node) for node in review.nodes],
        items=[
            ReviewItemOut(
                node_code=item["node_code"],
                node_title=item["node_title"],
                title=item["title"],
                kind=item["kind"],
                score=item["score"],
                answered=item["answered"],
                correct=item["correct"],
                criteria=[ReviewCriterionOut(**c) for c in item["criteria"]],
                feedback_short=item["feedback_short"],
                llm_fallback=item["llm_fallback"],
            )
            for item in review.items
        ],
    )


@router.get(
    "/sessions/{session_id}/items/current",
    response_model=ItemCurrentOut,
    summary="Текущее задание сессии (без эталонов и рубрик)",
)
async def get_current_test_item(
    session_id: UUID, user: CandidateUser, session: Session
) -> ItemCurrentOut:
    current = await sessions_service.get_current_item(session, user, session_id)
    payload = current.payload
    options = sessions_service.shuffled_options(payload, str(current.item.id))
    return ItemCurrentOut(
        index=current.index,
        total=current.total,
        item=ItemBodyOut(
            id=current.item.id,
            node_code=current.item.node_id,
            title=str(payload.get("title") or current.item.node_id),
            body_md=sessions_service.body_md(payload),
            kind=sessions_service.api_kind(payload),  # type: ignore[arg-type]
            options=(
                [ItemOptionOut(id=str(o["id"]), text=str(o["text"])) for o in options]
                if options
                else None
            ),
            time_limit_seconds=payload.get("time_limit_seconds"),
        ),
    )


@router.post(
    "/sessions/{session_id}/items/{item_id}/answer",
    response_model=AnswerOut,
    summary="Сохранить ответ на задание (оценка — на finish)",
    responses={409: {"description": "item_already_answered — ответ уже принят"}},
)
async def answer_test_item(
    session_id: UUID, item_id: UUID, payload: AnswerIn, user: CandidateUser, session: Session
) -> AnswerOut:
    result = await sessions_service.submit_answer(
        session, user, session_id, item_id, text=payload.text, choice_id=payload.choice_id
    )
    return AnswerOut(accepted=True, finished=result.finished, next_index=result.next_index)


@router.post(
    "/sessions/{session_id}/finish",
    response_model=FinishOut,
    summary="Завершить сессию: грейдинг, подтверждения, категория, итог по узлам",
    responses={
        409: {
            "description": "grading_in_progress — оценка уже выполняется (ретрайте запрос); "
            "по завершённой сессии возвращается сохранённый итог (200, идемпотентно)"
        }
    },
)
async def finish_test_session(session_id: UUID, user: CandidateUser, session: Session) -> FinishOut:
    result = await sessions_service.finish_session(session, user, session_id)
    return FinishOut(
        status="graded",
        grade=result.grade,
        grade_confirmed=result.grade_confirmed,
        q_level=result.q_level,
        nodes=[FinishNodeOut(**node) for node in result.nodes],
        integrity=(
            FinishIntegrityOut(
                level=result.integrity["level"],
                flags=[IntegrityFlagOut(**flag) for flag in result.integrity["flags"]],
            )
            if result.integrity is not None
            else None
        ),
    )


router.include_router(_internal)
