"""Сервис модуля `candidates` (минимум дня 6): профиль кандидата.

- профиль создаётся при первом обращении `GET /candidates/me` (без ПДн, только псевдоним);
- псевдоним «B-7F3A» генерируется `core.pii.generate_pseudonym()`, уникальность —
  UNIQUE в БД, при коллизии генерируется заново (до `PSEUDONYM_ATTEMPTS` попыток);
- ПДн (ФИО, телефон, telegram) шифруются типом `EncryptedStr` при записи, blind index
  `*_hash` считается здесь через `get_pii_crypto().blind_index(attr, value)`;
- «о себе» пишется в `about_sanitized` только после `sanitize_for_llm`: контакты, ссылки
  и ФИО (в том числе известные из профиля — через `terms`) заменяются плейсхолдерами,
  поэтому колонку безопасно показывать работодателю и отдавать в промпты;
- запись ПДн — только при согласии (`security.require_consent`, проверяет роутер).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import conflict, validation_error
from app.core.pii import generate_pseudonym, get_pii_crypto
from app.models.candidate import DEFAULT_PRIVACY, CandidateCategory, CandidateProfile
from app.models.skills import Industry
from app.models.user import AppUser
from app.modules.candidates.schemas import CandidateMeOut, PrivacyOut
from app.modules.llm.sanitizer import PLACEHOLDER_FIO, sanitize_for_llm
from app.skills.loader import get_catalog as get_skills_catalog

PSEUDONYM_ATTEMPTS = 5

# атрибут ПДн → имя колонки blind index
PII_HASH_ATTRS: dict[str, str] = {
    "full_name": "full_name_hash",
    "phone": "phone_hash",
    "telegram": "telegram_hash",
}


def build_me(profile: CandidateProfile) -> CandidateMeOut:
    privacy = {**DEFAULT_PRIVACY, **(profile.privacy or {})}
    return CandidateMeOut(
        id=profile.id,
        pseudonym=profile.pseudonym,
        full_name=profile.full_name,
        phone=profile.phone,
        telegram=profile.telegram,
        city=profile.city,
        industry_code=profile.industry_code,
        specialization=profile.specialization_code,
        stack_tags_declared=list(profile.stack_tags_declared or []),
        stack_tags_confirmed=list(profile.stack_tags_confirmed or []),
        experience_years=profile.experience_years,
        roles_declared=list(profile.roles_declared or []),
        soft_skills_declared=list(profile.soft_skills_declared or []),
        about=profile.about_sanitized,
        salary_floor=profile.salary_floor,
        privacy=PrivacyOut(**privacy),
        searchable=profile.searchable,
        fsp_id=profile.fsp_id,
        fsp_linked_at=profile.fsp_linked_at,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


async def find_profile(session: AsyncSession, user_id: Any) -> CandidateProfile | None:
    return (
        await session.execute(select(CandidateProfile).where(CandidateProfile.user_id == user_id))
    ).scalar_one_or_none()


async def get_or_create_profile(session: AsyncSession, user: AppUser) -> CandidateProfile:
    """Профиль кандидата; при первом обращении создаётся пустой профиль с псевдонимом.

    Коллизия псевдонима (UNIQUE) ловится как IntegrityError внутри savepoint и
    повторяется с новым значением; иные IntegrityError (например, гонка двух первых
    запросов одного пользователя по `user_id`) → 409.
    """
    profile = await find_profile(session, user.id)
    if profile is not None:
        return profile
    last_error: IntegrityError | None = None
    for _ in range(PSEUDONYM_ATTEMPTS):
        profile = CandidateProfile(
            user_id=user.id,
            pseudonym=generate_pseudonym(),
            privacy=dict(DEFAULT_PRIVACY),
            searchable=False,
            stack_tags_declared=[],
            stack_tags_confirmed=[],
            roles_declared=[],
            soft_skills_declared=[],
        )
        try:
            async with session.begin_nested():
                session.add(profile)
                await session.flush()
        except IntegrityError as exc:
            last_error = exc
            if "pseudonym" not in str(exc.orig or exc):
                break
            continue
        await session.commit()
        return profile
    # сюда попадаем при исчерпании попыток или чужом IntegrityError
    existing = await find_profile(session, user.id)
    if existing is not None:
        return existing
    raise conflict("Не удалось создать профиль, повторите запрос") from last_error


def apply_pii(profile: CandidateProfile, attr: str, value: str | None) -> None:
    """Присваивает ПДн-атрибут и пересчитывает его blind index (None → None)."""
    hash_attr = PII_HASH_ATTRS[attr]
    setattr(profile, attr, value)
    setattr(profile, hash_attr, get_pii_crypto().blind_index(attr, value) if value else None)


MIN_TERM_LENGTH = 3


def profile_pii_terms(profile: CandidateProfile) -> dict[str, str]:
    """Известные ПДн профиля → плейсхолдеры для санитайзера «о себе»: ФИО целиком и каждое
    его слово (имя без отчества регулярки санитайзера не ловят)."""
    terms: dict[str, str] = {}
    if profile.full_name:
        terms[profile.full_name] = PLACEHOLDER_FIO
        for part in profile.full_name.split():
            if len(part) >= MIN_TERM_LENGTH:
                terms[part] = PLACEHOLDER_FIO
    return terms


def sanitize_about(profile: CandidateProfile, value: str | None) -> str | None:
    """Текст «о себе» без контактов и ФИО; пустой результат → None."""
    if value is None:
        return None
    cleaned = sanitize_for_llm(value, terms=profile_pii_terms(profile)).strip()
    return cleaned or None


async def update_profile(
    session: AsyncSession, profile: CandidateProfile, changes: dict[str, Any]
) -> CandidateProfile:
    """Применяет изменения из `CandidateMeIn.changes()`; ПДн — через `apply_pii`,
    «о себе» — через санитайзер (после ПДн, чтобы учесть новое ФИО), `privacy` —
    слиянием ключей с пересчётом эффективного флага searchable (ПДн §3.7:
    privacy.searchable И категория присвоена). Порядок запросов при privacy:
    [отрасль] → категория (наличие)."""
    if "industry_code" in changes and changes["industry_code"] is not None:
        if await session.get(Industry, changes["industry_code"]) is None:
            raise validation_error(
                "Неизвестная отрасль",
                [{"field": "industry_code", "code": "unknown", "message": "Нет такой отрасли"}],
            )
    if "specialization" in changes:
        value = changes.pop("specialization")
        if value is not None and get_skills_catalog().spec(value) is None:
            raise validation_error(
                "Неизвестная специализация",
                [
                    {
                        "field": "specialization",
                        "code": "unknown",
                        "message": "Код специализации — из GET /skills/specializations",
                    }
                ],
            )
        profile.specialization_code = value
    if "privacy" in changes:
        incoming = changes.pop("privacy") or {}
        privacy = {**DEFAULT_PRIVACY, **(profile.privacy or {}), **incoming}
        profile.privacy = privacy
        # эффективная видимость в банке: privacy.searchable И категория есть (PLAN.md §3.7)
        has_category = (
            await session.execute(
                select(CandidateCategory)
                .where(CandidateCategory.candidate_id == profile.id)
                .limit(1)
            )
        ).scalar_one_or_none() is not None
        profile.searchable = bool(privacy.get("searchable", True)) and has_category
    for attr, value in changes.items():
        if attr == "about":
            continue
        if attr in PII_HASH_ATTRS:
            apply_pii(profile, attr, value)
        else:
            setattr(profile, attr, value)
    if "about" in changes:
        profile.about_sanitized = sanitize_about(profile, changes["about"])
    await session.commit()
    return profile
