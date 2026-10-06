"""Сервис модуля `auth`: выбор роли, согласие 152-ФЗ, удаление аккаунта.

Все изменения пишутся в `audit_log` в той же транзакции (`core.audit.write_audit`);
ПДн в аудит не попадают (только факты и версии). Commit — здесь, явный.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.errors import conflict
from app.core.security import ROLE_ADMIN, SELECTABLE_ROLES, check_roles, persisted_roles
from app.models.candidate import CandidateProfile
from app.models.employer import Company
from app.models.user import AppUser
from app.modules.auth.permissions import permissions_for_roles
from app.modules.auth.schemas import ConsentOut, MeResponse


def build_me(user: AppUser) -> MeResponse:
    """Ответ `GET /auth/me` из строки `app_user` (email — расшифрованный атрибут);
    роли — эффективные: сохранённые в БД ∪ роли токена (`admin` только из токена)."""
    roles = user.effective_roles
    return MeResponse(
        id=user.id,
        email=user.email,
        email_verified=user.email_verified,
        roles=roles,
        active_role=user.active_role,
        consent=ConsentOut(
            pdn_at=user.consent_pdn_at,
            policy_version=user.consent_policy_version,
            publish_at=user.consent_publish_at,
        ),
        created_at=user.created_at,
        permissions=permissions_for_roles(roles),
    )


def _now() -> datetime:
    return datetime.now(UTC)


async def choose_role(
    session: AsyncSession, user: AppUser, role: str, *, ip: str | None
) -> AppUser:
    """Выбор роли при первом входе: одна из `SELECTABLE_ROLES`, один раз.

    Повторный выбор (роль уже есть) — 409 `role_already_set`; исключение — `admin`
    (назначается только в Keycloak и в БД не хранится): ему разрешено добавлять себе роли
    и переключать активную. В строку пишутся только candidate/employer.
    """
    if role not in SELECTABLE_ROLES:
        raise conflict("Такую роль выбрать нельзя", code="role_not_selectable")
    effective = user.effective_roles
    is_admin = check_roles(effective, (ROLE_ADMIN,))
    if not is_admin and check_roles(effective, SELECTABLE_ROLES):
        raise conflict("Роль уже выбрана", code="role_already_set")
    before = {"roles": sorted(user.roles or []), "active_role": user.active_role}
    if role not in (user.roles or []):
        user.roles = persisted_roles({*(user.roles or []), role})
    user.active_role = role
    await write_audit(
        session,
        actor_user_id=user.id,
        action="role.selected",
        entity_type="app_user",
        entity_id=user.id,
        before=before,
        after={"roles": user.roles, "active_role": user.active_role},
        ip=ip,
    )
    await session.commit()
    return user


async def give_consent(
    session: AsyncSession,
    user: AppUser,
    *,
    policy_version: str,
    publish: bool,
    ip: str | None,
) -> AppUser:
    """Согласие на обработку ПДн (момент + версия политики) и, отдельно, на публикацию
    профиля работодателям. `publish=False` отзывает согласие на публикацию."""
    now = _now()
    before = {
        "consent_pdn_at": _iso(user.consent_pdn_at),
        "consent_policy_version": user.consent_policy_version,
        "consent_publish_at": _iso(user.consent_publish_at),
    }
    if user.consent_pdn_at is None or user.consent_policy_version != policy_version:
        user.consent_pdn_at = now
        user.consent_policy_version = policy_version
    if publish and user.consent_publish_at is None:
        user.consent_publish_at = now
    elif not publish and user.consent_publish_at is not None:
        user.consent_publish_at = None
    after = {
        "consent_pdn_at": _iso(user.consent_pdn_at),
        "consent_policy_version": user.consent_policy_version,
        "consent_publish_at": _iso(user.consent_publish_at),
    }
    await write_audit(
        session,
        actor_user_id=user.id,
        action="consent.given"
        if publish or before["consent_pdn_at"] is None
        else "consent.updated",
        entity_type="app_user",
        entity_id=user.id,
        before=before,
        after=after,
        ip=ip,
    )
    await session.commit()
    return user


async def delete_account(session: AsyncSession, user: AppUser, *, ip: str | None) -> None:
    """Отзыв согласия и удаление ПДн (152-ФЗ): строка `app_user` остаётся ради FK и аудита,
    но все ПДн и согласия стираются, профиль кандидата и контакты компании обезличиваются,
    кандидат исчезает из поиска. Учётная запись Keycloak не удаляется (Admin API —
    опциональный клиент `fsp-backend-admin`, roadmap); вход после удаления даёт 403.
    """
    now = _now()
    profile = (
        await session.execute(select(CandidateProfile).where(CandidateProfile.user_id == user.id))
    ).scalar_one_or_none()
    if profile is not None:
        profile.full_name = None
        profile.full_name_hash = None
        profile.phone = None
        profile.phone_hash = None
        profile.telegram = None
        profile.telegram_hash = None
        profile.about_sanitized = None
        profile.city = None
        profile.salary_floor = None
        profile.fsp_id = None
        profile.fsp_linked_at = None
        profile.searchable = False
        profile.privacy = {**(profile.privacy or {}), "searchable": False}
    company = (
        await session.execute(select(Company).where(Company.user_id == user.id))
    ).scalar_one_or_none()
    if company is not None:
        company.contact_email = None
        company.contact_email_hash = None
        company.contact_phone = None
        company.contact_phone_hash = None
    user.email = None
    user.email_hash = None
    user.email_verified = False
    user.username = f"deleted-{str(user.id)[:8]}"
    user.consent_pdn_at = None
    user.consent_policy_version = None
    user.consent_publish_at = None
    user.deleted_at = now
    await write_audit(
        session,
        actor_user_id=user.id,
        action="account.deleted",
        entity_type="app_user",
        entity_id=user.id,
        after={
            "deleted_at": now.isoformat(),
            "candidate_profile_anonymized": profile is not None,
            "company_contacts_erased": company is not None,
        },
        ip=ip,
    )
    await session.commit()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
