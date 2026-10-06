"""Сид демо-подбора: аккаунт жюри-кандидата, 28 синтетических кандидатов c
подтверждёнными узлами (ПДн — вымышленные), демо-воронка и приглашения.

Запускается после `employers` (SEED_ORDER): компании и потребности уже есть.
Детерминирован (`random.Random(2026)`) и идемпотентен: кандидат с существующим
профилем пропускается целиком, воронка и приглашения — upsert по натуральным парам.

Категории здесь намеренно создаются без `test_session` (поле `source_session_id = NULL`,
в `history` — пометка `seed`): это демо-данные для сценария работодателя, «честный» путь
через сессии — `POST /internal/candidates/bulk` (PLAN.md §7.5), не этот сид.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pii import get_pii_crypto
from app.models.candidate import (
    DEFAULT_PRIVACY,
    CandidateCategory,
    CandidateProfile,
    CandidateSkillState,
    SkillConfirmation,
)
from app.models.employer import (
    Company,
    ContactGrant,
    EmployerNeed,
    Invitation,
    NeedPipelineEntry,
    PipelineStageEvent,
)
from app.modules.employers.seed import (
    DEMO_COMPANY_NAME,
    NEED_MIDDLE_TITLE,
    ensure_user,
)

JUDGE_CANDIDATE_EMAIL = "judge-candidate@fsp.local"
SYNTHETIC_COUNT = 28
HALF_LIFE_DAYS = 180
# узлы по грейдам (подмножества графа be, согласованы с grade_profiles.yaml)
GRADE_NODES: dict[str, list[tuple[str, int]]] = {
    "junior": [("be.B1", 1), ("be.B2", 1), ("be.B3", 1), ("be.D1", 1)],
    "middle": [
        ("be.B1", 2),
        ("be.B2", 2),
        ("be.B3", 1),
        ("be.B4", 2),
        ("be.D1", 2),
        ("be.D4", 2),
        ("be.C1", 1),
    ],
    "senior": [
        ("be.B1", 3),
        ("be.B2", 2),
        ("be.B4", 3),
        ("be.B5", 2),
        ("be.D3", 2),
        ("be.D4", 2),
        ("be.C1", 2),
        ("be.C2", 2),
        ("be.C3", 2),
    ],
}
GRADE_SHAPES = {"junior": "I", "middle": "T", "senior": "P"}
STACKS = ["python", "fastapi", "postgresql", "docker", "redis", "kafka"]
_PSEUDONYM_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def grade_for(index: int) -> str:
    if index < 12:
        return "junior"
    if index < 22:
        return "middle"
    return "senior"


def pseudonym_for(index: int) -> str:
    """Детерминированный псевдоним в формате «B-7F3A» (сид, без коллизий по построению)."""
    letter = _PSEUDONYM_LETTERS[index % len(_PSEUDONYM_LETTERS)]
    return f"{letter}-{0xD000 + index:04X}"


def decayed(w0: float, age_days: int) -> float:
    return round(w0 * 0.5 ** (age_days / HALF_LIFE_DAYS), 4)


async def ensure_candidate(
    session: AsyncSession,
    *,
    suffix: str,
    email: str,
    username: str,
    full_name: str,
    pseudonym: str,
    grade: str,
    nodes: list[tuple[str, int]],
    rng: random.Random,
    salary_floor: int | None = None,
) -> tuple[CandidateProfile, bool]:
    """Кандидат целиком: пользователь, профиль, подтверждения, снимки, категория."""
    user, _ = await ensure_user(
        session, suffix=suffix, email=email, username=username, roles=["candidate"]
    )
    profile = (
        await session.execute(select(CandidateProfile).where(CandidateProfile.user_id == user.id))
    ).scalar_one_or_none()
    if profile is not None:
        return profile, False
    crypto = get_pii_crypto()
    now = datetime.now(UTC)
    phone = f"+7999{rng.randint(0, 9_999_999):07d}"
    stack = sorted(rng.sample(STACKS, k=min(len(STACKS), 2 + rng.randint(0, 2))))
    profile = CandidateProfile(
        user_id=user.id,
        pseudonym=pseudonym,
        full_name=full_name,
        full_name_hash=crypto.blind_index("full_name", full_name),
        phone=phone,
        phone_hash=crypto.blind_index("phone", phone),
        city=rng.choice(["Москва", "Казань", "Новосибирск", "Екатеринбург", None]),
        stack_tags_declared=stack,
        stack_tags_confirmed=stack[:2],
        experience_years={"junior": 1, "middle": 3, "senior": 6}[grade] + rng.randint(0, 2),
        privacy=dict(DEFAULT_PRIVACY),
        searchable=True,
        salary_floor=salary_floor,
    )
    session.add(profile)
    await session.flush()

    strengths: list[float] = []
    for node_id, level in nodes:
        age = rng.randint(5, 120)
        w0 = round(rng.uniform(0.82, 1.0), 4)
        w_now = decayed(w0, age)
        confirmed_at = now - timedelta(days=age)
        session.add(
            SkillConfirmation(
                candidate_id=profile.id,
                node_id=node_id,
                level=level,
                w0=Decimal(str(w0)),
                source="test",
                confirmed_at=confirmed_at,
                half_life_days=HALF_LIFE_DAYS,
            )
        )
        active_level = level if w_now >= 0.5 else max(level - 1, 0)
        session.add(
            CandidateSkillState(
                candidate_id=profile.id,
                node_id=node_id,
                active_level=active_level,
                strength_by_level={str(level): w_now},
                w_now=Decimal(str(w_now)),
                freshest_at=confirmed_at,
                computed_at=now,
            )
        )
        strengths.append(w_now)
    avg_strength = round(sum(strengths) / len(strengths), 4) if strengths else 0.0
    session.add(
        CandidateCategory(
            candidate_id=profile.id,
            specialization_code="be",
            grade=grade,
            q_level=max(len(nodes) - 1, 1),
            shape=GRADE_SHAPES[grade],
            strength=Decimal(str(avg_strength)),
            computed_at=now,
            history=[{"source": "seed", "at": now.isoformat()}],
        )
    )
    return profile, True


async def _ensure_entry(
    session: AsyncSession,
    need: EmployerNeed,
    candidate_id: uuid.UUID,
    stages: list[str],
    invitation_id: uuid.UUID | None = None,
    note: str | None = None,
) -> bool:
    """Строка воронки со всей историей переходов (`stages` — путь от начала)."""
    existing = (
        await session.execute(
            select(NeedPipelineEntry).where(
                NeedPipelineEntry.need_id == need.id,
                NeedPipelineEntry.candidate_id == candidate_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    entry = NeedPipelineEntry(
        need_id=need.id,
        candidate_id=candidate_id,
        stage=stages[-1],
        invitation_id=invitation_id,
        note=note,
    )
    session.add(entry)
    await session.flush()
    previous: str | None = None
    for stage in stages:
        session.add(PipelineStageEvent(entry_id=entry.id, from_stage=previous, to_stage=stage))
        previous = stage
    return True


async def _ensure_invitation(
    session: AsyncSession,
    *,
    company: Company,
    need: EmployerNeed | None,
    profile: CandidateProfile,
    status: str,
    salary_from: int,
    salary_to: int,
    with_grant: bool = False,
) -> tuple[Invitation | None, bool]:
    existing = (
        await session.execute(
            select(Invitation).where(
                Invitation.company_id == company.id,
                Invitation.candidate_id == profile.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    now = datetime.now(UTC)
    explanation: dict[str, Any] = {
        "text": f"Приглашение по потребности «{need.title}»" if need else "Приглашение из банка"
    }
    if need is not None:
        explanation["need_id"] = str(need.id)
    invitation = Invitation(
        company_id=company.id,
        candidate_id=profile.id,
        need_id=need.id if need else None,
        title="Приглашение в команду «ФСП.Орбита Демо»",
        offer_text="Ищем бэкенд-разработчика в команду платформы — ваш профиль закрывает "
        "ключевые узлы потребности.",
        salary_from=salary_from,
        salary_to=salary_to,
        contact_method="Telegram @orbita_demo_hr",
        explanation=explanation,
        status=status,
        sent_at=now - timedelta(days=1),
        viewed_at=now - timedelta(hours=20) if status in ("viewed", "accepted") else None,
        decided_at=now - timedelta(hours=2) if status in ("accepted", "declined") else None,
        expires_at=now + timedelta(days=13),
    )
    session.add(invitation)
    await session.flush()
    if with_grant and status == "accepted":
        session.add(
            ContactGrant(
                employer_user_id=company.user_id,
                candidate_id=profile.id,
                basis="invitation",
                ref_id=invitation.id,
                granted_at=now - timedelta(hours=2),
            )
        )
    return invitation, True


async def seed(session: AsyncSession) -> dict[str, int]:
    rng = random.Random(2026)
    counters = {"candidates": 0, "pipeline": 0, "invitations": 0}

    profiles: list[CandidateProfile] = []
    for i in range(SYNTHETIC_COUNT):
        grade = grade_for(i)
        nodes = list(GRADE_NODES[grade])
        if rng.random() < 0.3 and len(nodes) > 3:
            nodes.pop(rng.randrange(len(nodes)))  # «дырка» в подграфе — для объяснимости
        salary_floor = 200_000 if grade == "middle" and i % 3 == 0 else None
        profile, created = await ensure_candidate(
            session,
            suffix=f"cand-{i:02d}",
            email=f"demo-cand-{i:02d}@fsp.local",
            username=f"demo-cand-{i:02d}",
            full_name=f"Демо Кандидат {i:02d}",
            pseudonym=pseudonym_for(i),
            grade=grade,
            nodes=nodes,
            rng=rng,
            salary_floor=salary_floor,
        )
        profiles.append(profile)
        counters["candidates"] += int(created)

    judge_profile, created = await ensure_candidate(
        session,
        suffix="judge-candidate",
        email=JUDGE_CANDIDATE_EMAIL,
        username="judge-candidate",
        full_name="Жюри Кандидат",
        pseudonym="J-7F3A",
        grade="middle",
        nodes=[("be.B1", 2), ("be.B2", 2), ("be.B4", 2), ("be.D1", 2), ("be.D4", 1)],
        rng=rng,
    )
    counters["candidates"] += int(created)

    company = (
        await session.execute(select(Company).where(Company.name == DEMO_COMPANY_NAME))
    ).scalar_one_or_none()
    if company is None:
        return counters  # employers-сид не отработал — воронку не на чем строить
    need = (
        await session.execute(
            select(EmployerNeed).where(
                EmployerNeed.company_id == company.id,
                EmployerNeed.title == NEED_MIDDLE_TITLE,
            )
        )
    ).scalar_one_or_none()
    if need is None:
        return counters

    middle = profiles[12:22]
    # шорт-лист
    counters["pipeline"] += int(
        await _ensure_entry(
            session, need, middle[0].id, ["shortlist"], note="Сильное покрытие must-узлов"
        )
    )
    # отправленное приглашение
    inv_sent, created = await _ensure_invitation(
        session,
        company=company,
        need=need,
        profile=middle[1],
        status="sent",
        salary_from=190_000,
        salary_to=240_000,
    )
    counters["invitations"] += int(created)
    counters["pipeline"] += int(
        await _ensure_entry(
            session,
            need,
            middle[1].id,
            ["shortlist", "invited"],
            invitation_id=inv_sent.id if inv_sent else None,
        )
    )
    # принятое приглашение (контакты раскрыты через grant)
    inv_accepted, created = await _ensure_invitation(
        session,
        company=company,
        need=need,
        profile=middle[2],
        status="accepted",
        salary_from=200_000,
        salary_to=250_000,
        with_grant=True,
    )
    counters["invitations"] += int(created)
    counters["pipeline"] += int(
        await _ensure_entry(
            session,
            need,
            middle[2].id,
            ["shortlist", "invited", "accepted"],
            invitation_id=inv_accepted.id if inv_accepted else None,
        )
    )
    # собеседование (после принятия)
    inv_interview, created = await _ensure_invitation(
        session,
        company=company,
        need=need,
        profile=middle[3],
        status="accepted",
        salary_from=195_000,
        salary_to=245_000,
        with_grant=True,
    )
    counters["invitations"] += int(created)
    counters["pipeline"] += int(
        await _ensure_entry(
            session,
            need,
            middle[3].id,
            ["shortlist", "invited", "accepted", "interview"],
            invitation_id=inv_interview.id if inv_interview else None,
        )
    )
    # входящее приглашение для аккаунта жюри-кандидата
    inv_judge, created = await _ensure_invitation(
        session,
        company=company,
        need=need,
        profile=judge_profile,
        status="sent",
        salary_from=190_000,
        salary_to=250_000,
    )
    counters["invitations"] += int(created)
    counters["pipeline"] += int(
        await _ensure_entry(
            session,
            need,
            judge_profile.id,
            ["shortlist", "invited"],
            invitation_id=inv_judge.id if inv_judge else None,
        )
    )
    return counters
