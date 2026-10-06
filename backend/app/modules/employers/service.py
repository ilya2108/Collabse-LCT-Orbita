"""Сервис модуля `employers`: компания (личный/корпоративный профиль), участники
с приглашением без входа на платформу, минимальный CRUD потребностей.

Ключевые решения:
- при первом обращении employer-пользователя автоматически создаётся ЛИЧНАЯ компания
  (kind=personal) + строка владельца в `company_member` — порог входа нулевой;
- приглашение участника доступно только корпоративному профилю (личный → 409
  `company_kind_personal`, фронт предлагает переключить kind);
- одноразовый invite-токен генерируется `secrets.token_urlsafe` и в БД хранится только
  его HMAC (`PiiCrypto.hmac_hex`); в ответе токен показывается ровно один раз;
- ПДн приглашённых шифруются `EncryptedStr`, наружу отдаются маскированными;
- принятие приглашения привязывает `user_id`, дописывает роль `employer` и гасит токен.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.config import get_settings
from app.core.errors import conflict, forbidden, not_found, validation_error
from app.core.pii import get_pii_crypto, mask_email, mask_full_name
from app.models.employer import Company, CompanyMember, EmployerNeed
from app.models.skills import Industry, Specialization
from app.models.user import AppUser
from app.modules.candidates.schemas import clean_text
from app.modules.employers.schemas import (
    CompanyOut,
    MemberInviteIn,
    MemberOut,
    NeedIn,
    NeedOut,
)
from app.modules.llm.sanitizer import sanitize_for_llm

# роли, которым доступно управление участниками и редактирование компании
MANAGER_ROLES = ("owner", "hr")
INVITE_TOKEN_BYTES = 24
DEFAULT_PERSONAL_COMPANY_NAME = "Личный профиль работодателя"


def mask_telegram(value: str) -> str:
    """«orbita_hr» → «o***r» — для списков участников."""
    if len(value) <= 2:
        return f"{value[:1]}***"
    return f"{value[0]}***{value[-1]}"


def build_member(member: CompanyMember) -> MemberOut:
    return MemberOut(
        id=member.id,
        role=member.role,
        status=member.status,
        full_name_masked=mask_full_name(member.full_name) if member.full_name else None,
        email_masked=mask_email(member.email) if member.email else None,
        telegram_masked=mask_telegram(member.telegram) if member.telegram else None,
        invite_channel=member.invite_channel,
        has_account=member.user_id is not None,
        invited_at=member.invited_at,
        accepted_at=member.accepted_at,
    )


def build_company(company: Company, my_role: str, members_count: int | None = None) -> CompanyOut:
    return CompanyOut(
        id=company.id,
        kind=company.kind,
        name=company.name,
        inn=company.inn,
        description=company.description,
        industry_code=company.industry_code,
        website=company.website,
        contact_email=company.contact_email,
        contact_phone=company.contact_phone,
        my_role=my_role,
        members_count=members_count,
        created_at=company.created_at,
        updated_at=company.updated_at,
    )


def build_need(need: EmployerNeed) -> NeedOut:
    return NeedOut(
        id=need.id,
        company_id=need.company_id,
        title=need.title,
        raw_text=need.raw_text_sanitized,
        first_months_tasks=need.first_months_tasks,
        specialization=need.specialization_code,
        grade=need.grade,
        stack_tags=list(need.stack_tags or []),
        industry_code=need.industry_code,
        salary_from=need.salary_from,
        salary_to=need.salary_to,
        format=need.format,
        status=need.status,
        created_at=need.created_at,
        updated_at=need.updated_at,
    )


# --- компания ---------------------------------------------------------------------------


async def find_owned_company(session: AsyncSession, user_id: uuid.UUID) -> Company | None:
    return (
        await session.execute(select(Company).where(Company.user_id == user_id))
    ).scalar_one_or_none()


async def find_membership(session: AsyncSession, user_id: uuid.UUID) -> CompanyMember | None:
    return (
        await session.execute(
            select(CompanyMember).where(
                CompanyMember.user_id == user_id, CompanyMember.status == "active"
            )
        )
    ).scalar_one_or_none()


async def get_or_create_company(session: AsyncSession, user: AppUser) -> tuple[Company, str]:
    """Компания текущего работодателя и его роль в ней.

    Порядок: своя компания (owner) → активное участие (hr/hiring_manager) → создание
    личной компании. Запросы к сессии идут строго в этом порядке (важно для тестов).
    """
    company = await find_owned_company(session, user.id)
    if company is not None:
        return company, "owner"
    membership = await find_membership(session, user.id)
    if membership is not None:
        company = await session.get(Company, membership.company_id)
        if company is not None:
            return company, membership.role
    company = Company(
        user_id=user.id,
        name=user.username or DEFAULT_PERSONAL_COMPANY_NAME,
        kind="personal",
    )
    session.add(company)
    await session.flush()
    session.add(
        CompanyMember(
            company_id=company.id,
            user_id=user.id,
            role="owner",
            status="active",
            accepted_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return company, "owner"


async def count_active_members(session: AsyncSession, company_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(CompanyMember)
            .where(CompanyMember.company_id == company_id, CompanyMember.status != "revoked")
        )
    ).scalar() or 0


def apply_company_pii(company: Company, attr: str, value: str | None) -> None:
    """ПДн компании (contact_email/contact_phone): значение + blind index."""
    setattr(company, attr, value)
    hash_value = get_pii_crypto().blind_index(attr, value) if value else None
    setattr(company, f"{attr}_hash", hash_value)


async def update_company(
    session: AsyncSession, company: Company, my_role: str, changes: dict[str, Any]
) -> Company:
    """Применяет частичный PUT; редактировать могут owner и hr.

    Смена kind corporate → personal разрешена только пока в компании один активный
    участник (иначе 409 `company_has_members`).
    """
    if my_role not in MANAGER_ROLES:
        raise forbidden("Редактировать компанию может владелец или HR")
    if "industry_code" in changes and changes["industry_code"] is not None:
        if await session.get(Industry, changes["industry_code"]) is None:
            raise validation_error(
                "Неизвестная отрасль",
                [{"field": "industry_code", "code": "unknown", "message": "Нет такой отрасли"}],
            )
    new_kind = changes.get("kind")
    if new_kind == "personal" and company.kind == "corporate":
        if await count_active_members(session, company.id) > 1:
            raise conflict(
                "Нельзя переключиться на личный профиль: в компании есть участники",
                code="company_has_members",
            )
    for attr, value in changes.items():
        if attr in ("contact_email", "contact_phone"):
            apply_company_pii(company, attr, value)
        else:
            setattr(company, attr, value)
    await session.commit()
    return company


# --- участники --------------------------------------------------------------------------


async def list_members(session: AsyncSession, company_id: uuid.UUID) -> list[CompanyMember]:
    return list(
        (
            await session.execute(
                select(CompanyMember)
                .where(CompanyMember.company_id == company_id, CompanyMember.status != "revoked")
                .order_by(CompanyMember.created_at)
            )
        )
        .scalars()
        .all()
    )


def invite_url_for(token: str) -> str:
    return f"{get_settings().frontend_public_url}/employer/invite/{token}"


async def invite_member(
    session: AsyncSession,
    company: Company,
    my_role: str,
    actor: AppUser,
    payload: MemberInviteIn,
    ip: str | None = None,
) -> tuple[CompanyMember, str]:
    """Создаёт участника со статусом `invited` и возвращает (участник, одноразовый токен).

    Email-рассылку не городим (решение задачи): токен отдаётся в ответе, ссылку/код
    передаёт сам работодатель (или телеграм-бот). Дубликат по контакту → 409.
    """
    if my_role not in MANAGER_ROLES:
        raise forbidden("Приглашать участников может владелец или HR")
    if company.kind != "corporate":
        raise conflict(
            "Приглашения доступны корпоративному профилю: переключите тип компании",
            code="company_kind_personal",
        )
    crypto = get_pii_crypto()
    email_hash = crypto.blind_index("email", payload.email) if payload.email else None
    telegram_hash = crypto.hmac_hex(payload.telegram.strip().lower()) if payload.telegram else None
    duplicate = (
        await session.execute(
            select(CompanyMember).where(
                CompanyMember.company_id == company.id,
                CompanyMember.status != "revoked",
                (CompanyMember.email_hash == email_hash)
                if email_hash
                else (CompanyMember.telegram_hash == telegram_hash),
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise conflict(
            "Этот участник уже приглашён или состоит в компании", code="member_already_invited"
        )
    token = secrets.token_urlsafe(INVITE_TOKEN_BYTES)
    member = CompanyMember(
        company_id=company.id,
        role=payload.role,
        status="invited",
        full_name=payload.full_name,
        full_name_hash=crypto.blind_index("full_name", payload.full_name)
        if payload.full_name
        else None,
        email=payload.email,
        email_hash=email_hash,
        telegram=payload.telegram,
        telegram_hash=telegram_hash,
        invite_channel=payload.channel,
        invite_token_hash=crypto.hmac_hex(token),
        invited_by=actor.id,
        invited_at=datetime.now(UTC),
    )
    session.add(member)
    await session.flush()
    await write_audit(
        session,
        actor_user_id=actor.id,
        action="company_member_invited",
        entity_type="company_member",
        entity_id=member.id,
        after={
            "role": payload.role,
            "channel": payload.channel,
            "email": mask_email(payload.email) if payload.email else None,
            "telegram": mask_telegram(payload.telegram) if payload.telegram else None,
        },
        ip=ip,
    )
    await session.commit()
    return member, token


async def revoke_member(
    session: AsyncSession,
    company: Company,
    my_role: str,
    actor: AppUser,
    member_id: uuid.UUID,
    ip: str | None = None,
) -> None:
    """Отзыв участника/приглашения: статус `revoked`, invite-токен гасится."""
    if my_role not in MANAGER_ROLES:
        raise forbidden("Отзывать участников может владелец или HR")
    member = (
        await session.execute(
            select(CompanyMember).where(
                CompanyMember.id == member_id, CompanyMember.company_id == company.id
            )
        )
    ).scalar_one_or_none()
    if member is None or member.status == "revoked":
        raise not_found("Участник не найден")
    if member.role == "owner":
        raise conflict("Владельца компании нельзя отозвать", code="owner_not_revocable")
    member.status = "revoked"
    member.revoked_at = datetime.now(UTC)
    member.invite_token_hash = None
    await write_audit(
        session,
        actor_user_id=actor.id,
        action="company_member_revoked",
        entity_type="company_member",
        entity_id=member.id,
        ip=ip,
    )
    await session.commit()


async def find_member_by_token(session: AsyncSession, token: str) -> CompanyMember | None:
    token_hash = get_pii_crypto().hmac_hex(token)
    return (
        await session.execute(
            select(CompanyMember).where(CompanyMember.invite_token_hash == token_hash)
        )
    ).scalar_one_or_none()


async def accept_invite(
    session: AsyncSession, user: AppUser, token: str, ip: str | None = None
) -> tuple[Company, CompanyMember]:
    """Принятие приглашения авторизованным пользователем: привязка `user_id`,
    статус `active`, роль `employer` дописывается в `app_user.roles`."""
    member = await find_member_by_token(session, token)
    if member is None or member.status != "invited":
        raise not_found("Приглашение не найдено или уже использовано")
    company = await session.get(Company, member.company_id)
    if company is None:
        raise not_found("Компания не найдена")
    member.user_id = user.id
    member.status = "active"
    member.accepted_at = datetime.now(UTC)
    member.invite_token_hash = None
    if "employer" not in (user.roles or []):
        user.roles = sorted({*(user.roles or []), "employer"})
        session.add(user)
    await write_audit(
        session,
        actor_user_id=user.id,
        action="company_member_accepted",
        entity_type="company_member",
        entity_id=member.id,
        ip=ip,
    )
    await session.commit()
    return company, member


# --- потребности ------------------------------------------------------------------------


def sanitize_need_text(value: str | None) -> str | None:
    """Текст потребности хранится только после санитайзера (контакты → плейсхолдеры)."""
    if value is None:
        return None
    cleaned = sanitize_for_llm(value).strip()
    return clean_text(cleaned)


async def create_need(session: AsyncSession, company: Company, payload: NeedIn) -> EmployerNeed:
    if await session.get(Specialization, payload.specialization) is None:
        raise validation_error(
            "Неизвестная специализация",
            [
                {
                    "field": "specialization",
                    "code": "unknown",
                    "message": "Нет такой специализации",
                }
            ],
        )
    need = EmployerNeed(
        company_id=company.id,
        title=payload.title,
        raw_text_sanitized=sanitize_need_text(payload.raw_text),
        first_months_tasks=sanitize_need_text(payload.first_months_tasks),
        specialization_code=payload.specialization,
        grade=payload.grade,
        stack_tags=payload.stack_tags or [],
        industry_code=payload.industry_code,
        salary_from=payload.salary_from,
        salary_to=payload.salary_to,
        format=payload.format,
        status="active",
    )
    session.add(need)
    await session.flush()
    await session.commit()
    return need


async def list_needs(session: AsyncSession, company_id: uuid.UUID) -> list[EmployerNeed]:
    return list(
        (
            await session.execute(
                select(EmployerNeed)
                .where(EmployerNeed.company_id == company_id)
                .order_by(EmployerNeed.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


async def get_company_need(
    session: AsyncSession, company_id: uuid.UUID, need_id: uuid.UUID
) -> EmployerNeed:
    """Потребность своей компании; чужая/несуществующая → 404 (без утечки существования)."""
    need = (
        await session.execute(
            select(EmployerNeed).where(
                EmployerNeed.id == need_id, EmployerNeed.company_id == company_id
            )
        )
    ).scalar_one_or_none()
    if need is None:
        raise not_found("Потребность не найдена")
    return need


async def update_need(
    session: AsyncSession, need: EmployerNeed, changes: dict[str, Any]
) -> EmployerNeed:
    """Частичное обновление; зарплатный инвариант проверяется по итоговым значениям."""
    salary_from = changes.get("salary_from", need.salary_from)
    salary_to = changes.get("salary_to", need.salary_to)
    if salary_from is not None and salary_to is not None and salary_from > salary_to:
        raise validation_error(
            "Зарплата «от» не может быть больше зарплаты «до»",
            [{"field": "salary_from", "code": "salary_range", "message": "from > to"}],
        )
    for attr, value in changes.items():
        if attr in ("raw_text", "first_months_tasks"):
            value = sanitize_need_text(value)
        if attr == "raw_text":
            need.raw_text_sanitized = value
        else:
            setattr(need, attr, value)
    await session.commit()
    return need
