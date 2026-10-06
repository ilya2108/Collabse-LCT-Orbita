"""Дайджест «ФСП.Орбита»: чистые сборщики текста + коллекторы данных из БД.

Разделение намеренное (тестируемость без PostgreSQL):
- `build_candidate_digest` / `build_employer_digest` — чистые функции
  «датакласс → русский текст», юнит-тестируются напрямую;
- `collect_candidate_digest` / `collect_employer_digest` — читают БД и собирают
  датаклассы; переиспользуют ранжирование модуля matching (та же формула и
  объяснимость, что в вебе).

ПДн: в датаклассах кандидаты представлены ТОЛЬКО псевдонимами («B-7F3A»); контактов
и ФИО здесь нет по построению. Финальный текст дополнительно проходит санитайзер
в `service.deliver`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import CandidateCategory, CandidateProfile, CandidateSkillState
from app.models.employer import Company, EmployerNeed, Invitation
from app.modules.matching import service as matching_service

GRADE_TITLES = {"junior": "джуниор", "middle": "мидл", "senior": "сеньор"}
INVITATION_STATUS_TITLES = {
    "sent": "ожидает ответа",
    "viewed": "просмотрено",
    "accepted": "принято",
    "declined": "отклонено",
    "expired": "истекло",
}
STAGE_TITLES = {
    "shortlist": "шорт-лист",
    "invited": "приглашены",
    "accepted": "приняли",
    "interview": "интервью",
    "offer": "оффер",
    "hired": "наняты",
    "declined": "отказ",
}
TOP_LIMIT = 5


def format_salary(salary_from: int | None, salary_to: int | None) -> str:
    """80000, 130000 → «80–130 тыс. ₽»; без значений — «з/п не указана»."""
    if not salary_from or not salary_to:
        return "з/п не указана"
    return f"{salary_from // 1000}–{salary_to // 1000} тыс. ₽"


# --- кандидат ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DigestVacancy:
    """Подходящая потребность/вакансия для кандидата (данные без ПДн)."""

    need_id: uuid.UUID
    title: str
    company_name: str
    grade: str | None
    salary_from: int | None
    salary_to: int | None
    score: float | None
    why: str  # объяснение «почему вы» (§5.4)


@dataclass(frozen=True)
class DigestInvitation:
    invitation_id: uuid.UUID
    company_name: str
    title: str
    status: str
    salary_from: int
    salary_to: int


@dataclass(frozen=True)
class CandidateDigest:
    pseudonym: str
    grade: str | None
    specialization: str | None
    vacancies: list[DigestVacancy] = field(default_factory=list)
    invitations: list[DigestInvitation] = field(default_factory=list)


def build_candidate_digest(data: CandidateDigest) -> str:
    """Текст дайджеста кандидата: топ-5 подходящих вакансий + статусы приглашений."""
    lines = ["ФСП.Орбита — ваш дайджест", ""]
    if data.grade and data.specialization:
        grade = GRADE_TITLES.get(data.grade, data.grade)
        lines.append(f"Профиль: {data.pseudonym} · {data.specialization} · {grade}")
        lines.append("")
    if data.vacancies:
        lines.append("Подходящие вакансии:")
        for index, vacancy in enumerate(data.vacancies[:TOP_LIMIT], start=1):
            salary = format_salary(vacancy.salary_from, vacancy.salary_to)
            match = f" · match {vacancy.score:.0%}" if vacancy.score is not None else ""
            lines.append(f"{index}. «{vacancy.title}» — {vacancy.company_name} · {salary}{match}")
            if vacancy.why:
                lines.append(f"   Почему вы: {vacancy.why}")
    else:
        lines.append(
            "Новых подходящих вакансий пока нет — загляните позже или обновите категорию тестом."
        )
    if data.invitations:
        lines.append("")
        lines.append("Ваши приглашения:")
        for invitation in data.invitations:
            status = INVITATION_STATUS_TITLES.get(invitation.status, invitation.status)
            salary = format_salary(invitation.salary_from, invitation.salary_to)
            lines.append(
                f"— «{invitation.title}» от {invitation.company_name} · {salary} · {status}"
            )
    return "\n".join(lines)


async def collect_candidate_digest(
    session: AsyncSession, profile: CandidateProfile
) -> CandidateDigest:
    """Данные дайджеста кандидата. Порядок запросов фиксирован (важно для тестов):
    категория → активные потребности → компании → снимки узлов → приглашения → компании."""
    category = (
        await session.execute(
            select(CandidateCategory).where(CandidateCategory.candidate_id == profile.id)
        )
    ).scalar_one_or_none()
    vacancies: list[DigestVacancy] = []
    if category is not None:
        needs = (
            (
                await session.execute(
                    select(EmployerNeed)
                    .where(
                        EmployerNeed.status == "active",
                        EmployerNeed.specialization_code == category.specialization_code,
                    )
                    .order_by(EmployerNeed.created_at.desc())
                    .limit(20)
                )
            )
            .scalars()
            .all()
        )
        company_names: dict[uuid.UUID, str] = {}
        if needs:
            companies = (
                (
                    await session.execute(
                        select(Company).where(Company.id.in_({n.company_id for n in needs}))
                    )
                )
                .scalars()
                .all()
            )
            company_names = {c.id: c.name for c in companies}
        node_map: dict[uuid.UUID, list[matching_service.NeedNode]] = {}
        all_node_ids: set[str] = set()
        for need in needs:
            nodes, _basis = await matching_service.resolve_need_nodes(session, need)
            node_map[need.id] = nodes
            all_node_ids.update(n.id for n in nodes)
        active_levels: dict[str, int] = {}
        if all_node_ids:
            states = (
                (
                    await session.execute(
                        select(CandidateSkillState).where(
                            CandidateSkillState.candidate_id == profile.id,
                            CandidateSkillState.node_id.in_(all_node_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            active_levels = {s.node_id: s.active_level for s in states}
        facts = matching_service.CandidateFacts(
            candidate_id=profile.id,
            pseudonym=profile.pseudonym,
            grade=category.grade,
            q_level=category.q_level,
            shape=category.shape,
            strength=float(category.strength or 0),
            active_levels=active_levels,
        )
        for need in needs:
            ranked = matching_service.rank_candidates(node_map[need.id], need.grade, [facts])
            item = ranked[0]
            vacancies.append(
                DigestVacancy(
                    need_id=need.id,
                    title=need.title,
                    company_name=company_names.get(need.company_id, "компания"),
                    grade=need.grade,
                    salary_from=need.salary_from,
                    salary_to=need.salary_to,
                    score=item.score,
                    why=item.explanation.text,
                )
            )
        vacancies.sort(key=lambda v: -(v.score or 0))
        vacancies = vacancies[:TOP_LIMIT]
    raw_invitations, names = await matching_service.list_candidate_invitations(session, profile)
    invitations = [
        DigestInvitation(
            invitation_id=inv.id,
            company_name=names.get(inv.company_id, "компания"),
            title=inv.title,
            status=inv.status,
            salary_from=inv.salary_from,
            salary_to=inv.salary_to,
        )
        for inv in raw_invitations[:TOP_LIMIT]
    ]
    return CandidateDigest(
        pseudonym=profile.pseudonym,
        grade=category.grade if category else None,
        specialization=category.specialization_code if category else None,
        vacancies=vacancies,
        invitations=invitations,
    )


# --- работодатель / HR ------------------------------------------------------------------


@dataclass(frozen=True)
class DigestCandidate:
    """Кандидат в дайджесте работодателя: ТОЛЬКО псевдоним (контакты — после принятия)."""

    pseudonym: str
    grade: str
    score: float
    grade_confirmed: bool


@dataclass(frozen=True)
class DigestNeed:
    need_id: uuid.UUID
    title: str
    grade: str | None
    candidates: list[DigestCandidate] = field(default_factory=list)
    pipeline_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class EmployerDigest:
    company_name: str
    needs: list[DigestNeed] = field(default_factory=list)


def build_employer_digest(data: EmployerDigest) -> str:
    """Текст дайджеста работодателя: топ кандидатов под каждую активную потребность.

    Кандидаты — псевдонимами, без контактов (контакты открываются на платформе после
    принятия приглашения кандидатом)."""
    lines = [f"ФСП.Орбита — дайджест для «{data.company_name}»", ""]
    if not data.needs:
        lines.append("Активных потребностей нет. Создайте потребность на платформе —")
        lines.append("и бот начнёт присылать подходящих кандидатов.")
        return "\n".join(lines)
    for need in data.needs:
        grade = f" · {GRADE_TITLES.get(need.grade, need.grade)}" if need.grade else ""
        lines.append(f"Потребность «{need.title}»{grade}:")
        if need.candidates:
            for index, cand in enumerate(need.candidates[:TOP_LIMIT], start=1):
                mark = "" if cand.grade_confirmed else " · грейд не совпадает"
                lines.append(
                    f"{index}. {cand.pseudonym} · {GRADE_TITLES.get(cand.grade, cand.grade)}"
                    f" · match {cand.score:.0%}{mark}"
                )
        else:
            lines.append("— подходящих кандидатов пока нет")
        if need.pipeline_counts:
            summary = " · ".join(
                f"{STAGE_TITLES.get(stage, stage)}: {count}"
                for stage, count in need.pipeline_counts.items()
                if count
            )
            if summary:
                lines.append(f"Воронка: {summary}")
        lines.append("")
    return "\n".join(lines).rstrip()


async def collect_employer_digest(session: AsyncSession, company: Company) -> EmployerDigest:
    """Данные дайджеста работодателя: по каждой активной потребности — топ-5 кандидатов
    (ранжирование модуля matching) и сводка воронки."""
    needs = (
        (
            await session.execute(
                select(EmployerNeed)
                .where(EmployerNeed.company_id == company.id, EmployerNeed.status == "active")
                .order_by(EmployerNeed.created_at.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    digest_needs: list[DigestNeed] = []
    for need in needs:
        nodes, _basis = await matching_service.resolve_need_nodes(session, need)
        candidates = await matching_service.collect_candidates(
            session, need, company.id, [n.id for n in nodes]
        )
        ranked = matching_service.rank_candidates(nodes, need.grade, candidates)
        entries = await matching_service.get_pipeline_entries(session, need.id)
        counts: dict[str, int] = {}
        for entry in entries:
            counts[entry.stage] = counts.get(entry.stage, 0) + 1
        digest_needs.append(
            DigestNeed(
                need_id=need.id,
                title=need.title,
                grade=need.grade,
                candidates=[
                    DigestCandidate(
                        pseudonym=item.pseudonym,
                        grade=item.grade,
                        score=item.score,
                        grade_confirmed=item.grade_confirmed,
                    )
                    for item in ranked[:TOP_LIMIT]
                ],
                pipeline_counts=counts,
            )
        )
    return EmployerDigest(company_name=company.name, needs=digest_needs)


# --- общая точка ------------------------------------------------------------------------


async def build_digest_for_link(
    session: AsyncSession, link_role: str, subject: CandidateProfile | Company
) -> str:
    """Текст дайджеста по роли привязки: кандидату — вакансии, работодателю — кандидаты.

    `subject` — CandidateProfile для кандидата или Company для работодателя/HR.
    """
    if link_role == "candidate" and isinstance(subject, CandidateProfile):
        return build_candidate_digest(await collect_candidate_digest(session, subject))
    if isinstance(subject, Company):
        return build_employer_digest(await collect_employer_digest(session, subject))
    raise ValueError("роль привязки не соответствует переданному субъекту")


def invitation_push_text(company_name: str, invitation: Invitation) -> str:
    """Текст пуша кандидату о новом приглашении (зарплата «от–до», без контактов)."""
    salary = format_salary(invitation.salary_from, invitation.salary_to)
    why = (invitation.explanation or {}).get("text") or ""
    lines = [
        "ФСП.Орбита — новое приглашение!",
        f"«{invitation.title}» от {company_name} · {salary}",
    ]
    if why:
        lines.append(f"Почему вы: {why}")
    lines.append("Принять или отклонить можно кнопками ниже.")
    return "\n".join(lines)


def invitation_buttons(invitation_id: uuid.UUID) -> list[list[dict[str, str]]]:
    """Кнопки пуша о приглашении (callback-данные укладываются в 64 байта)."""
    from app.modules.tgbot.service import pack_uuid

    packed = pack_uuid(invitation_id)
    return [
        [
            {"text": "Принять", "callback_data": f"a:{packed}"},
            {"text": "Отклонить", "callback_data": f"d:{packed}"},
        ]
    ]
