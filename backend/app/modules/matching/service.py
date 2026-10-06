"""Сервис модуля `matching`: честная заглушка ранжирования, мини-CRM и приглашения.

Ранжирование (до формулы §5.3): кандидаты сортируются по доле подтверждённых узлов
подграфа потребности (взвешенной важностью must/should/nice); кандидаты, чей грейд
не совпадает с запрошенным (или не подтверждён тестом), идут ниже в выдаче — так
рекомендовали организаторы. Если подграфа ещё нет, узлы берутся из профиля грейда,
а без них — сортировка по силе профиля. Поле `explanation` объясняет каждый пункт.

Мини-CRM: стадии `shortlist → invited → accepted → interview → offer → hired | declined`;
`invited` и `accepted` проставляет только сервис приглашений, остальное работодатель
двигает вручную (валидация переходов — `can_transition`). Контакты кандидата раскрываются
исключительно после принятия приглашения — через `contact_grant` с записью в
`contact_reveal_audit` (правило организаторов).

Лимиты приглашений (§5.6 п. 4): 10 незакрытых на потребность, 10 незакрытых без
потребности, 30 на компанию, 5 новых кандидату в сутки, одно открытое на пару
(компания, кандидат); приглашение протухает через 14 дней.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import ApiError, conflict, forbidden, not_found
from app.core.pii import mask_full_name
from app.models.candidate import CandidateCategory, CandidateProfile, CandidateSkillState
from app.models.employer import (
    Company,
    EmployerNeed,
    Invitation,
    NeedPipelineEntry,
    NeedVersion,
    PipelineStageEvent,
)
from app.models.skills import GradeProfile
from app.models.user import AppUser
from app.modules.assessment.integrity import latest_integrity_by_candidate
from app.modules.matching.schemas import (
    BankCandidateOut,
    ExplanationOut,
    InvitationOut,
    PipelineItemOut,
    RankedCandidateOut,
)

# --- ранжирование (честная заглушка до формулы §5.3) ------------------------------------

IMPORTANCE_WEIGHTS = {"must": 1.0, "should": 0.6, "nice": 0.3}
MAX_CANDIDATES = 300


@dataclass(frozen=True)
class NeedNode:
    """Узел подграфа потребности."""

    id: str
    min_level: int = 1
    importance: str = "must"


@dataclass
class CandidateFacts:
    """Данные кандидата для ранжирования (читаются из снимков, без ПДн)."""

    candidate_id: uuid.UUID
    pseudonym: str
    grade: str
    q_level: int
    shape: str
    strength: float
    active_levels: dict[str, int] = field(default_factory=dict)
    # целостность последней завершённой сессии теста; None — данных нет
    integrity_level: str | None = None


def need_nodes_from_subgraph(subgraph: dict[str, Any] | None) -> list[NeedNode]:
    """Поддерживает оба формата снимка подграфа:
    `{"nodes": [{"id", "min_level", "importance"}]}` и `{"must": [...], "nice": [...]}`."""
    if not subgraph:
        return []
    nodes: list[NeedNode] = []
    for raw in subgraph.get("nodes") or []:
        if isinstance(raw, dict) and raw.get("id"):
            nodes.append(
                NeedNode(
                    id=str(raw["id"]),
                    min_level=int(raw.get("min_level") or 1),
                    importance=str(raw.get("importance") or "must"),
                )
            )
    if nodes:
        return nodes
    for importance in ("must", "should", "nice"):
        for node_id in subgraph.get(importance) or []:
            nodes.append(NeedNode(id=str(node_id), importance=importance))
    return nodes


def node_match(active_level: int, min_level: int) -> float:
    """1 — узел активен на нужном уровне; 0.5 — на уровень ниже; 0 — не подтверждён."""
    if active_level >= min_level:
        return 1.0
    if active_level > 0 and active_level == min_level - 1:
        return 0.5
    return 0.0


def rank_candidates(
    nodes: list[NeedNode], need_grade: str | None, candidates: list[CandidateFacts]
) -> list[RankedCandidateOut]:
    """Детерминированное ранжирование: покрытие подграфа, затем подтверждённый грейд.

    Кандидаты с несовпадающим грейдом (`grade_confirmed = False`) всегда ниже любых
    кандидатов с подтверждённым — отдельной «полкой», независимо от score.
    """
    basis = "subgraph" if nodes else "profile_strength"
    items: list[RankedCandidateOut] = []
    total_weight = sum(IMPORTANCE_WEIGHTS.get(n.importance, 1.0) for n in nodes)
    for cand in candidates:
        grade_confirmed = need_grade is None or cand.grade == need_grade
        matched: list[str] = []
        partial: list[str] = []
        missing: list[str] = []
        if nodes and total_weight > 0:
            score_sum = 0.0
            for node in nodes:
                match = node_match(cand.active_levels.get(node.id, 0), node.min_level)
                score_sum += IMPORTANCE_WEIGHTS.get(node.importance, 1.0) * match
                if match >= 1.0:
                    matched.append(node.id)
                elif match > 0:
                    partial.append(node.id)
                else:
                    missing.append(node.id)
            coverage = round(score_sum / total_weight, 4)
            score = coverage
        else:
            coverage = None
            score = round(min(1.0, max(0.0, cand.strength)), 4)
        items.append(
            RankedCandidateOut(
                candidate_id=cand.candidate_id,
                pseudonym=cand.pseudonym,
                score=score,
                coverage=coverage,
                grade=cand.grade,
                grade_confirmed=grade_confirmed,
                q_level=cand.q_level,
                shape=cand.shape,
                integrity_level=cand.integrity_level,  # type: ignore[arg-type]
                explanation=build_explanation(
                    basis, cand, matched, partial, missing, len(nodes), grade_confirmed
                ),
            )
        )
    items.sort(key=lambda i: (not i.grade_confirmed, -i.score, -i.q_level, i.pseudonym))
    return items


def build_explanation(
    basis: str,
    cand: CandidateFacts,
    matched: list[str],
    partial: list[str],
    missing: list[str],
    nodes_total: int,
    grade_confirmed: bool,
) -> ExplanationOut:
    """Карточка «Почему здесь» (§5.4) — собирается из данных, воспроизводимо, без LLM."""
    parts: list[str] = []
    if basis in ("subgraph", "grade_profile") and nodes_total:
        parts.append(f"Закрывает {len(matched)} из {nodes_total} узлов потребности")
        if partial:
            parts.append(f"на уровень ниже: {', '.join(partial)}")
        if missing:
            parts.append(f"не подтверждено: {', '.join(missing)} (проверить на собеседовании)")
    else:
        parts.append(f"Сортировка по силе подтверждённого профиля ({cand.strength:.2f})")
    parts.append(f"Уровень по графу: {cand.q_level} · форма {cand.shape}")
    if not grade_confirmed:
        parts.append(f"Грейд {cand.grade} отличается от запрошенного — ниже в выдаче")
    return ExplanationOut(
        basis=basis,  # type: ignore[arg-type]
        matched_nodes=matched,
        partial_nodes=partial,
        missing_nodes=missing,
        text=" · ".join(parts),
    )


async def latest_verified_version(session: AsyncSession, need_id: uuid.UUID) -> NeedVersion | None:
    return (
        await session.execute(
            select(NeedVersion)
            .where(NeedVersion.need_id == need_id, NeedVersion.mode == "verified")
            .order_by(NeedVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def resolve_need_nodes(
    session: AsyncSession, need: EmployerNeed
) -> tuple[list[NeedNode], str]:
    """Узлы для ранжирования: снимок последней версии → профиль грейда → сила профиля."""
    version = await latest_verified_version(session, need.id)
    nodes = need_nodes_from_subgraph(version.subgraph if version else None)
    if nodes:
        return nodes, "subgraph"
    if need.specialization_code and need.grade:
        profile = (
            await session.execute(
                select(GradeProfile).where(
                    GradeProfile.specialization_code == need.specialization_code,
                    GradeProfile.grade == need.grade,
                )
            )
        ).scalar_one_or_none()
        if profile is not None and profile.target_nodes:
            return [NeedNode(id=n) for n in profile.target_nodes], "grade_profile"
    return [], "profile_strength"


def _profile_visible(profile: CandidateProfile, company_id: uuid.UUID) -> bool:
    hidden = (profile.privacy or {}).get("hidden_company_ids") or []
    return profile.searchable and str(company_id) not in {str(c) for c in hidden}


async def collect_candidates(
    session: AsyncSession, need: EmployerNeed, company_id: uuid.UUID, node_ids: list[str]
) -> list[CandidateFacts]:
    """Кандидаты специализации потребности (все грейды) со снимками силы узлов.

    Порядок запросов фиксирован: категории → профили → снимки узлов →
    уровни целостности (важно для тестов).
    """
    categories = (
        (
            await session.execute(
                select(CandidateCategory)
                .where(CandidateCategory.specialization_code == need.specialization_code)
                .limit(MAX_CANDIDATES)
            )
        )
        .scalars()
        .all()
    )
    if not categories:
        return []
    candidate_ids = [c.candidate_id for c in categories]
    profiles = (
        (
            await session.execute(
                select(CandidateProfile).where(CandidateProfile.id.in_(candidate_ids))
            )
        )
        .scalars()
        .all()
    )
    profiles_by_id = {p.id: p for p in profiles if _profile_visible(p, company_id)}
    states_by_candidate: dict[uuid.UUID, dict[str, int]] = {}
    if node_ids:
        states = (
            (
                await session.execute(
                    select(CandidateSkillState).where(
                        CandidateSkillState.candidate_id.in_(candidate_ids),
                        CandidateSkillState.node_id.in_(node_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        for state in states:
            states_by_candidate.setdefault(state.candidate_id, {})[state.node_id] = (
                state.active_level
            )
    result: list[CandidateFacts] = []
    for category in categories:
        profile = profiles_by_id.get(category.candidate_id)
        if profile is None:
            continue
        result.append(
            CandidateFacts(
                candidate_id=category.candidate_id,
                pseudonym=profile.pseudonym,
                grade=category.grade,
                q_level=category.q_level,
                shape=category.shape,
                strength=float(category.strength or 0),
                active_levels=states_by_candidate.get(category.candidate_id, {}),
            )
        )
    if result:
        levels = await latest_integrity_by_candidate(session, [c.candidate_id for c in result])
        for facts in result:
            facts.integrity_level = levels.get(facts.candidate_id)
    return result


# --- банк кандидатов ----------------------------------------------------------------------

# порядок грейдов в банке: senior выше middle выше junior
BANK_GRADE_ORDER = {"senior": 0, "middle": 1, "junior": 2}
BANK_STACK_LIMIT = 8


async def list_bank_candidates(
    session: AsyncSession,
    company_id: uuid.UUID,
    specialization: str | None = None,
    grade: str | None = None,
) -> list[BankCandidateOut]:
    """Банк кандидатов: все, у кого есть категория и `searchable=true`, под псевдонимами.

    Видимость — та же логика, что в подборке (`_profile_visible`): скрытые от компании
    кандидаты не показываются. Сортировка: грейд (senior > middle > junior), затем
    q_level по убыванию, затем свежесть категории. Порядок запросов фиксирован:
    категории → профили (важно для тестов).
    """
    stmt = select(CandidateCategory)
    if specialization:
        stmt = stmt.where(CandidateCategory.specialization_code == specialization)
    if grade:
        stmt = stmt.where(CandidateCategory.grade == grade)
    categories = (await session.execute(stmt.limit(MAX_CANDIDATES))).scalars().all()
    if not categories:
        return []
    profiles = (
        (
            await session.execute(
                select(CandidateProfile).where(
                    CandidateProfile.id.in_({c.candidate_id for c in categories})
                )
            )
        )
        .scalars()
        .all()
    )
    profiles_by_id = {p.id: p for p in profiles if _profile_visible(p, company_id)}
    items: list[BankCandidateOut] = []
    for category in categories:
        profile = profiles_by_id.get(category.candidate_id)
        if profile is None:
            continue
        items.append(
            BankCandidateOut(
                candidate_id=category.candidate_id,
                pseudonym=profile.pseudonym,
                specialization_code=category.specialization_code,
                grade=category.grade,
                q_level=category.q_level,
                shape=category.shape,
                grade_confirmed_at=category.computed_at,
                stack_confirmed=list(profile.stack_tags_confirmed or [])[:BANK_STACK_LIMIT],
            )
        )
    items.sort(
        key=lambda i: (
            BANK_GRADE_ORDER.get(i.grade, len(BANK_GRADE_ORDER)),
            -i.q_level,
            -(i.grade_confirmed_at.timestamp() if i.grade_confirmed_at else 0.0),
            i.pseudonym,
        )
    )
    return items


# --- мини-CRM ---------------------------------------------------------------------------

PIPELINE_STAGE_ORDER = (
    "shortlist",
    "invited",
    "accepted",
    "interview",
    "offer",
    "hired",
    "declined",
)
# контакты доступны только после принятия приглашения кандидатом
CONTACTS_AVAILABLE_STAGES = frozenset({"accepted", "interview", "offer", "hired"})
# target-стадия → допустимые текущие стадии (ручные переводы работодателя)
ALLOWED_MANUAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "shortlist": frozenset({"declined"}),
    "interview": frozenset({"accepted", "offer", "hired"}),
    "offer": frozenset({"accepted", "interview", "hired"}),
    "hired": frozenset({"accepted", "interview", "offer"}),
    "declined": frozenset(set(PIPELINE_STAGE_ORDER) - {"declined"}),
}


def can_transition(current: str, target: str) -> bool:
    """Ручной перевод стадии: interview/offer/hired — только после принятия приглашения
    (контакты раскрыты), declined — из любой стадии, shortlist — возврат после отказа."""
    return current in ALLOWED_MANUAL_TRANSITIONS.get(target, frozenset())


def build_pipeline_item(
    entry: NeedPipelineEntry,
    profile: CandidateProfile | None,
    invitation_status: str | None,
    score: float | None,
    integrity_level: str | None = None,
) -> PipelineItemOut:
    contacts_available = entry.stage in CONTACTS_AVAILABLE_STAGES
    full_name = profile.full_name if (profile and contacts_available) else None
    return PipelineItemOut(
        candidate_id=entry.candidate_id,
        pseudonym=profile.pseudonym if profile else "—",
        stage=entry.stage,
        note=entry.note,
        invitation_id=entry.invitation_id,
        invitation_status=invitation_status,
        contacts_available=contacts_available,
        full_name_masked=mask_full_name(full_name) if full_name else None,
        score=score,
        integrity_level=integrity_level,  # type: ignore[arg-type]
        updated_at=entry.updated_at,
    )


async def get_pipeline_entries(
    session: AsyncSession, need_id: uuid.UUID
) -> list[NeedPipelineEntry]:
    return list(
        (
            await session.execute(
                select(NeedPipelineEntry)
                .where(NeedPipelineEntry.need_id == need_id)
                .order_by(NeedPipelineEntry.created_at)
            )
        )
        .scalars()
        .all()
    )


def record_stage_event(
    session: AsyncSession,
    entry: NeedPipelineEntry,
    from_stage: str | None,
    to_stage: str,
    actor_user_id: uuid.UUID | None,
    comment: str | None = None,
) -> None:
    entry.stage = to_stage
    session.add(
        PipelineStageEvent(
            entry_id=entry.id,
            from_stage=from_stage,
            to_stage=to_stage,
            actor_user_id=actor_user_id,
            comment=comment,
        )
    )


async def add_to_pipeline(
    session: AsyncSession,
    need: EmployerNeed,
    actor: AppUser,
    candidate_id: uuid.UUID,
    note: str | None,
    ip: str | None = None,
) -> NeedPipelineEntry:
    """Добавляет кандидата в шорт-лист потребности (409, если уже в воронке)."""
    profile = (
        await session.execute(select(CandidateProfile).where(CandidateProfile.id == candidate_id))
    ).scalar_one_or_none()
    if profile is None:
        raise not_found("Кандидат не найден")
    existing = (
        await session.execute(
            select(NeedPipelineEntry).where(
                NeedPipelineEntry.need_id == need.id,
                NeedPipelineEntry.candidate_id == candidate_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise conflict(
            f"Кандидат уже в воронке (стадия {existing.stage})", code="pipeline_entry_exists"
        )
    entry = NeedPipelineEntry(
        need_id=need.id, candidate_id=candidate_id, stage="shortlist", note=note
    )
    session.add(entry)
    await session.flush()
    record_stage_event(session, entry, None, "shortlist", actor.id)
    await write_audit(
        session,
        actor_user_id=actor.id,
        action="pipeline_entry_created",
        entity_type="need_pipeline_entry",
        entity_id=entry.id,
        after={"stage": "shortlist"},
        ip=ip,
    )
    await session.commit()
    return entry


async def set_pipeline_stage(
    session: AsyncSession,
    need: EmployerNeed,
    actor: AppUser,
    candidate_id: uuid.UUID,
    target_stage: str,
    comment: str | None,
    ip: str | None = None,
) -> NeedPipelineEntry:
    """Ручной перевод стадии с валидацией переходов и записью истории."""
    entry = (
        await session.execute(
            select(NeedPipelineEntry).where(
                NeedPipelineEntry.need_id == need.id,
                NeedPipelineEntry.candidate_id == candidate_id,
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        raise not_found("Кандидат не найден в воронке потребности")
    if entry.stage == target_stage:
        return entry
    if not can_transition(entry.stage, target_stage):
        raise conflict(
            f"Переход «{entry.stage} → {target_stage}» недопустим: стадии interview/offer/hired "
            "доступны после принятия приглашения кандидатом",
            code="pipeline_transition_invalid",
        )
    from_stage = entry.stage
    record_stage_event(session, entry, from_stage, target_stage, actor.id, comment)
    await write_audit(
        session,
        actor_user_id=actor.id,
        action="pipeline_stage_changed",
        entity_type="need_pipeline_entry",
        entity_id=entry.id,
        before={"stage": from_stage},
        after={"stage": target_stage},
        ip=ip,
    )
    await session.commit()
    return entry


# --- приглашения ------------------------------------------------------------------------

MAX_OPEN_PER_NEED = 10
MAX_OPEN_NO_NEED = 10
MAX_OPEN_PER_COMPANY = 30
MAX_CANDIDATE_PER_DAY = 5
INVITATION_TTL_DAYS = 14
OPEN_STATUSES = ("sent", "viewed")


def quota_error(
    open_pair: int, open_scope: int, scope_is_need: bool, open_total: int, candidate_daily: int
) -> ApiError | None:
    """Проверка лимитов §5.6 п. 4 (чистая функция, юнит-тестируется отдельно)."""
    if open_pair > 0:
        return conflict(
            "Кандидат уже приглашён этой компанией (есть незакрытое приглашение)",
            code="invitation_already_open",
        )
    scope_limit = MAX_OPEN_PER_NEED if scope_is_need else MAX_OPEN_NO_NEED
    scope_name = "на потребность" if scope_is_need else "без потребности"
    if open_scope >= scope_limit:
        error = conflict(
            f"Достигнут лимит незакрытых приглашений {scope_name} ({scope_limit})",
            code="invitation_quota",
        )
        error.details = [
            {"field": "need_id", "code": "scope_limit", "message": f"осталось 0 из {scope_limit}"}
        ]
        return error
    if open_total >= MAX_OPEN_PER_COMPANY:
        error = conflict(
            f"Достигнут лимит незакрытых приглашений компании ({MAX_OPEN_PER_COMPANY})",
            code="invitation_quota",
        )
        error.details = [
            {
                "field": "company",
                "code": "company_limit",
                "message": f"осталось 0 из {MAX_OPEN_PER_COMPANY}",
            }
        ]
        return error
    if candidate_daily >= MAX_CANDIDATE_PER_DAY:
        return conflict(
            "Кандидат уже получил максимум приглашений за сутки, попробуйте завтра",
            code="candidate_daily_limit",
        )
    return None


def build_invitation(
    invitation: Invitation,
    *,
    candidate_pseudonym: str | None = None,
    company_name: str | None = None,
    contact_method_visible: bool = False,
) -> InvitationOut:
    return InvitationOut(
        id=invitation.id,
        need_id=invitation.need_id,
        candidate_pseudonym=candidate_pseudonym,
        company_name=company_name,
        title=invitation.title,
        message=invitation.offer_text,
        salary_from=invitation.salary_from,
        salary_to=invitation.salary_to,
        contact_method=invitation.contact_method if contact_method_visible else None,
        explanation=dict(invitation.explanation or {}),
        status=invitation.status,
        sent_at=invitation.sent_at,
        viewed_at=invitation.viewed_at,
        decided_at=invitation.decided_at,
        expires_at=invitation.expires_at,
        decline_reason_code=invitation.decline_reason_code,
        decline_text=invitation.decline_text,
    )


async def _count(session: AsyncSession, stmt: Any) -> int:
    return (await session.execute(stmt)).scalar() or 0


async def create_invitation(
    session: AsyncSession,
    company: Company,
    actor: AppUser,
    *,
    candidate_id: uuid.UUID,
    need: EmployerNeed | None,
    title: str,
    message: str | None,
    salary_from: int,
    salary_to: int,
    contact_method: str | None,
    ip: str | None = None,
) -> tuple[Invitation, bool]:
    """Создаёт приглашение-снимок, двигает pipeline в `invited`, проверяет лимиты.

    Возвращает (приглашение, «до» ниже приватной планки кандидата). Порядок запросов:
    кандидат → открытая пара → счётчик области → счётчик компании → суточный счётчик
    кандидата → [pipeline-entry при наличии потребности].
    """
    candidate = (
        await session.execute(select(CandidateProfile).where(CandidateProfile.id == candidate_id))
    ).scalar_one_or_none()
    if candidate is None:
        raise not_found("Кандидат не найден")
    open_pair = await _count(
        session,
        select(func.count())
        .select_from(Invitation)
        .where(
            Invitation.company_id == company.id,
            Invitation.candidate_id == candidate_id,
            Invitation.status.in_(OPEN_STATUSES),
        ),
    )
    scope_filter = (
        Invitation.need_id == need.id if need is not None else Invitation.need_id.is_(None)
    )
    open_scope = await _count(
        session,
        select(func.count())
        .select_from(Invitation)
        .where(
            Invitation.company_id == company.id,
            scope_filter,
            Invitation.status.in_(OPEN_STATUSES),
        ),
    )
    open_total = await _count(
        session,
        select(func.count())
        .select_from(Invitation)
        .where(Invitation.company_id == company.id, Invitation.status.in_(OPEN_STATUSES)),
    )
    now = datetime.now(UTC)
    candidate_daily = await _count(
        session,
        select(func.count())
        .select_from(Invitation)
        .where(
            Invitation.candidate_id == candidate_id,
            Invitation.sent_at >= now - timedelta(days=1),
        ),
    )
    error = quota_error(open_pair, open_scope, need is not None, open_total, candidate_daily)
    if error is not None:
        raise error
    explanation: dict[str, Any] = {
        "text": f"Приглашение по потребности «{need.title}»" if need else "Приглашение из банка",
    }
    if need is not None:
        explanation["need_id"] = str(need.id)
    invitation = Invitation(
        company_id=company.id,
        candidate_id=candidate_id,
        need_id=need.id if need else None,
        title=title,
        offer_text=message,
        salary_from=salary_from,
        salary_to=salary_to,
        contact_method=contact_method,
        explanation=explanation,
        status="sent",
        sent_at=now,
        expires_at=now + timedelta(days=INVITATION_TTL_DAYS),
    )
    session.add(invitation)
    await session.flush()
    if need is not None:
        entry = (
            await session.execute(
                select(NeedPipelineEntry).where(
                    NeedPipelineEntry.need_id == need.id,
                    NeedPipelineEntry.candidate_id == candidate_id,
                )
            )
        ).scalar_one_or_none()
        if entry is None:
            entry = NeedPipelineEntry(need_id=need.id, candidate_id=candidate_id, stage="shortlist")
            session.add(entry)
            await session.flush()
            record_stage_event(session, entry, None, "shortlist", actor.id)
        entry.invitation_id = invitation.id
        if entry.stage in ("shortlist", "declined"):
            record_stage_event(session, entry, entry.stage, "invited", actor.id)
    await write_audit(
        session,
        actor_user_id=actor.id,
        action="invitation_sent",
        entity_type="invitation",
        entity_id=invitation.id,
        after={"candidate": candidate.pseudonym, "need_id": explanation.get("need_id")},
        ip=ip,
    )
    await session.commit()
    below_floor = bool(candidate.salary_floor and salary_to < candidate.salary_floor)
    return invitation, below_floor


async def get_candidate_profile(session: AsyncSession, user: AppUser) -> CandidateProfile:
    profile = (
        await session.execute(select(CandidateProfile).where(CandidateProfile.user_id == user.id))
    ).scalar_one_or_none()
    if profile is None:
        raise not_found("Профиль кандидата не найден")
    return profile


async def get_invitation_for_candidate(
    session: AsyncSession, profile: CandidateProfile, invitation_id: uuid.UUID
) -> Invitation:
    invitation = (
        await session.execute(select(Invitation).where(Invitation.id == invitation_id))
    ).scalar_one_or_none()
    if invitation is None or invitation.candidate_id != profile.id:
        raise not_found("Приглашение не найдено")
    return invitation


def _ensure_open(invitation: Invitation) -> None:
    """Решение возможно только по открытому и не протухшему приглашению."""
    if invitation.status not in OPEN_STATUSES:
        raise conflict(
            f"Приглашение уже закрыто (статус {invitation.status})", code="invitation_closed"
        )
    expires_at = invitation.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at < datetime.now(UTC):
            invitation.status = "expired"
            raise conflict("Срок действия приглашения истёк", code="invitation_expired")


async def _move_pipeline_for_invitation(
    session: AsyncSession, invitation: Invitation, to_stage: str, actor_id: uuid.UUID
) -> None:
    if invitation.need_id is None:
        return
    entry = (
        await session.execute(
            select(NeedPipelineEntry).where(
                NeedPipelineEntry.need_id == invitation.need_id,
                NeedPipelineEntry.candidate_id == invitation.candidate_id,
            )
        )
    ).scalar_one_or_none()
    if entry is not None and entry.stage != to_stage:
        record_stage_event(session, entry, entry.stage, to_stage, actor_id)


async def accept_invitation(
    session: AsyncSession, user: AppUser, invitation_id: uuid.UUID, ip: str | None = None
) -> tuple[Invitation, Company]:
    """Принятие: статус `accepted`, `contact_grant` на контакты кандидата, pipeline →
    accepted; кандидату открываются контакты пригласившей компании."""
    from app.models.employer import ContactGrant  # локально: единственная точка использования

    profile = await get_candidate_profile(session, user)
    invitation = await get_invitation_for_candidate(session, profile, invitation_id)
    _ensure_open(invitation)
    company = await session.get(Company, invitation.company_id)
    if company is None:
        raise not_found("Компания не найдена")
    now = datetime.now(UTC)
    invitation.status = "accepted"
    invitation.decided_at = now
    session.add(
        ContactGrant(
            employer_user_id=company.user_id,
            candidate_id=profile.id,
            basis="invitation",
            ref_id=invitation.id,
            granted_at=now,
        )
    )
    await _move_pipeline_for_invitation(session, invitation, "accepted", user.id)
    await write_audit(
        session,
        actor_user_id=user.id,
        action="invitation_accepted",
        entity_type="invitation",
        entity_id=invitation.id,
        ip=ip,
    )
    await session.commit()
    return invitation, company


async def decline_invitation(
    session: AsyncSession,
    user: AppUser,
    invitation_id: uuid.UUID,
    reason_code: str | None,
    text: str | None,
    ip: str | None = None,
) -> Invitation:
    profile = await get_candidate_profile(session, user)
    invitation = await get_invitation_for_candidate(session, profile, invitation_id)
    _ensure_open(invitation)
    invitation.status = "declined"
    invitation.decided_at = datetime.now(UTC)
    invitation.decline_reason_code = reason_code
    invitation.decline_text = text
    await _move_pipeline_for_invitation(session, invitation, "declined", user.id)
    await write_audit(
        session,
        actor_user_id=user.id,
        action="invitation_declined",
        entity_type="invitation",
        entity_id=invitation.id,
        after={"reason_code": reason_code},
        ip=ip,
    )
    await session.commit()
    return invitation


async def view_invitation(
    session: AsyncSession, user: AppUser, invitation_id: uuid.UUID
) -> Invitation:
    """Отметка «кандидат открыл приглашение»: sent → viewed."""
    profile = await get_candidate_profile(session, user)
    invitation = await get_invitation_for_candidate(session, profile, invitation_id)
    if invitation.status == "sent":
        invitation.status = "viewed"
        invitation.viewed_at = datetime.now(UTC)
        await session.commit()
    return invitation


async def reveal_candidate_contacts(
    session: AsyncSession,
    company: Company,
    actor: AppUser,
    invitation_id: uuid.UUID,
    ip: str | None = None,
) -> tuple[CandidateProfile, AppUser | None]:
    """Контакты кандидата работодателю: только при `contact_grant` (после принятия),
    с проверкой согласия на публикацию и записью в `contact_reveal_audit` (152-ФЗ)."""
    from app.models.employer import ContactGrant, ContactRevealAudit

    invitation = (
        await session.execute(select(Invitation).where(Invitation.id == invitation_id))
    ).scalar_one_or_none()
    if invitation is None or invitation.company_id != company.id:
        raise not_found("Приглашение не найдено")
    grant = (
        await session.execute(
            select(ContactGrant).where(
                ContactGrant.basis == "invitation",
                ContactGrant.ref_id == invitation.id,
                ContactGrant.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if grant is None:
        raise forbidden("Контакты доступны только после принятия приглашения кандидатом")
    profile = (
        await session.execute(
            select(CandidateProfile).where(CandidateProfile.id == invitation.candidate_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        raise not_found("Кандидат не найден")
    candidate_user = (
        await session.execute(select(AppUser).where(AppUser.id == profile.user_id))
    ).scalar_one_or_none()
    if candidate_user is None or candidate_user.consent_publish_at is None:
        raise forbidden("Кандидат не давал согласия на раскрытие контактов")
    session.add(
        ContactRevealAudit(
            grant_id=grant.id,
            employer_user_id=actor.id,
            candidate_id=profile.id,
            fields=["full_name", "email", "phone", "telegram"],
            ip=ip,
        )
    )
    await write_audit(
        session,
        actor_user_id=actor.id,
        action="contact_reveal",
        entity_type="candidate_profile",
        entity_id=profile.id,
        after={"basis": "invitation", "ref_id": str(invitation.id)},
        ip=ip,
    )
    await session.commit()
    return profile, candidate_user


async def list_company_invitations(
    session: AsyncSession, company_id: uuid.UUID
) -> tuple[list[Invitation], dict[uuid.UUID, str]]:
    """Приглашения компании + псевдонимы кандидатов (два запроса, порядок фиксирован)."""
    invitations = list(
        (
            await session.execute(
                select(Invitation)
                .where(Invitation.company_id == company_id)
                .order_by(Invitation.sent_at.desc())
            )
        )
        .scalars()
        .all()
    )
    pseudonyms: dict[uuid.UUID, str] = {}
    if invitations:
        candidate_ids = {i.candidate_id for i in invitations}
        profiles = (
            (
                await session.execute(
                    select(CandidateProfile).where(CandidateProfile.id.in_(candidate_ids))
                )
            )
            .scalars()
            .all()
        )
        pseudonyms = {p.id: p.pseudonym for p in profiles}
    return invitations, pseudonyms


async def list_candidate_invitations(
    session: AsyncSession, profile: CandidateProfile
) -> tuple[list[Invitation], dict[uuid.UUID, str]]:
    """Входящие приглашения кандидата + названия компаний."""
    invitations = list(
        (
            await session.execute(
                select(Invitation)
                .where(Invitation.candidate_id == profile.id)
                .order_by(Invitation.sent_at.desc())
            )
        )
        .scalars()
        .all()
    )
    names: dict[uuid.UUID, str] = {}
    if invitations:
        company_ids = {i.company_id for i in invitations}
        companies = (
            (await session.execute(select(Company).where(Company.id.in_(company_ids))))
            .scalars()
            .all()
        )
        names = {c.id: c.name for c in companies}
    return invitations, names
