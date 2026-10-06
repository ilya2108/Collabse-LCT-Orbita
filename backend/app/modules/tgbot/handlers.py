"""Обработчики телеграм-бота «ФСП.Орбита» (aiogram 3, один бот — две роли).

Команды:
- `/start <код>` — привязка чата: одноразовый код из `POST /auth/telegram/link-token`
  (кандидат или работодатель) ЛИБО invite-токен участника компании — тогда чат
  привязывается к `company_member` без аккаунта на платформе («HR через Telegram»);
- `/digest` — дайджест сейчас (кандидату — вакансии и приглашения, работодателю —
  кандидаты под потребности, псевдонимами);
- `/status` — кандидату: категория и грейд; работодателю: компания и роль;
- `/pipeline` — работодателю: сводка колонок мини-CRM по активным потребностям.

Inline-кнопки: `a:<id22>` / `d:<id22>` — принять/отклонить приглашение (кандидат),
`s:<need22>:<cand22>` / `i:<need22>:<cand22>` — в шортлист / пригласить (работодатель).

Зависимости (`session_factory`) передаются через workflow-data диспетчера; в тестах
обработчики вызываются напрямую с фейковыми Message/CallbackQuery и FakeSession.
ПДн: кандидат в ответах работодателю — только псевдоним; `tg_chat_id` в логи не пишется.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ApiError
from app.core.pii import mask_email, mask_phone
from app.models.candidate import CandidateCategory, CandidateProfile
from app.models.employer import Company, CompanyMember, EmployerNeed
from app.models.tgbot import TgLink
from app.models.user import AppUser
from app.modules.employers import service as employers_service
from app.modules.matching import service as matching_service
from app.modules.tgbot import digest as digest_module
from app.modules.tgbot import service as tg_service

router = Router(name="orbita")

HELP_TEXT = (
    "Это бот платформы «ФСП.Орбита» — обратный подбор ИТ-специалистов.\n\n"
    "Чтобы привязать аккаунт, получите код в личном кабинете на платформе\n"
    "(кнопка «Привязать Telegram») и откройте ссылку — или отправьте\n"
    "/start <код> вручную. HR может использовать invite-токен приглашения.\n\n"
    "Команды:\n"
    "/digest — дайджест сейчас\n"
    "/status — ваш профиль\n"
    "/pipeline — воронка найма (для работодателя)"
)

NOT_LINKED_TEXT = (
    "Чат ещё не привязан. Получите код на платформе «ФСП.Орбита» "
    "(кнопка «Привязать Telegram») и отправьте /start <код>."
)


async def _company_for_link(session: AsyncSession, link: TgLink) -> Company | None:
    """Компания по привязке работодателя: через участника (HR без аккаунта) или через
    пользователя (владелец/участник)."""
    if link.member_id is not None:
        member = await session.get(CompanyMember, link.member_id)
        if member is None:
            return None
        return await session.get(Company, member.company_id)
    if link.user_id is None:
        return None
    user = await session.get(AppUser, link.user_id)
    if user is None:
        return None
    company, _role = await employers_service.get_or_create_company(session, user)
    return company


# --- /start: привязка -------------------------------------------------------------------


@router.message(CommandStart())
async def on_start(
    message: Message,
    command: CommandObject,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    token = (command.args or "").strip()
    if not token:
        await message.answer(HELP_TEXT)
        return
    async with session_factory() as session:
        link_token = await tg_service.consume_link_token(session, token)
        if link_token is not None:
            await tg_service.bind_chat(
                session,
                message.chat.id,
                role=link_token.role,
                user_id=link_token.user_id,
            )
            role_title = "кандидата" if link_token.role == "candidate" else "работодателя"
            await message.answer(
                f"Готово! Чат привязан к профилю {role_title} на «ФСП.Орбита».\n"
                "Команда /digest пришлёт дайджест прямо сейчас."
            )
            return
        member = await employers_service.find_member_by_token(session, token)
        if member is not None and member.status == "invited":
            company = await session.get(Company, member.company_id)
            await tg_service.bind_member_chat(session, message.chat.id, member)
            company_name = company.name if company else "компания"
            role_title = "HR" if member.role == "hr" else "нанимающий менеджер"
            await message.answer(
                f"Вы приглашены в «{company_name}» как {role_title}.\n"
                "Чат привязан — дайджесты и воронка доступны без входа на платформу:\n"
                "/digest — кандидаты под потребности, /pipeline — воронка найма.\n"
                "Токен остаётся действительным для регистрации на платформе."
            )
            return
    await message.answer(
        "Код не найден или уже использован. Получите новый на платформе «ФСП.Орбита»."
    )


# --- /digest ----------------------------------------------------------------------------


@router.message(Command("digest"))
async def on_digest(message: Message, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        link = await tg_service.find_link(session, message.chat.id)
        if link is None:
            await message.answer(NOT_LINKED_TEXT)
            return
        if link.role == "candidate" and link.user_id is not None:
            profile = (
                await session.execute(
                    select(CandidateProfile).where(CandidateProfile.user_id == link.user_id)
                )
            ).scalar_one_or_none()
            if profile is None:
                await message.answer("Профиль кандидата не найден — заполните его на платформе.")
                return
            text = await digest_module.build_digest_for_link(session, "candidate", profile)
        else:
            company = await _company_for_link(session, link)
            if company is None:
                await message.answer("Компания не найдена — проверьте профиль на платформе.")
                return
            text = await digest_module.build_digest_for_link(session, "employer", company)
        await tg_service.deliver(
            session,
            None,
            tg_service.OutgoingMessage(
                tg_chat_id=message.chat.id, text=text, kind=tg_service.KIND_MANUAL_DIGEST
            ),
        )
    await message.answer(text)


# --- /status ----------------------------------------------------------------------------


@router.message(Command("status"))
async def on_status(message: Message, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        link = await tg_service.find_link(session, message.chat.id)
        if link is None:
            await message.answer(NOT_LINKED_TEXT)
            return
        if link.role == "candidate" and link.user_id is not None:
            profile = (
                await session.execute(
                    select(CandidateProfile).where(CandidateProfile.user_id == link.user_id)
                )
            ).scalar_one_or_none()
            if profile is None:
                await message.answer("Профиль кандидата не найден — заполните его на платформе.")
                return
            category = (
                await session.execute(
                    select(CandidateCategory).where(CandidateCategory.candidate_id == profile.id)
                )
            ).scalar_one_or_none()
            if category is None:
                await message.answer(
                    f"Профиль {profile.pseudonym}: категория ещё не присвоена — "
                    "пройдите тест на платформе."
                )
                return
            grade = digest_module.GRADE_TITLES.get(category.grade, category.grade)
            await message.answer(
                f"Профиль {profile.pseudonym}\n"
                f"Категория: {category.specialization_code} · {grade}\n"
                f"Уровень по графу (Q): {category.q_level} · форма {category.shape}"
            )
            return
        company = await _company_for_link(session, link)
        if company is None:
            await message.answer("Компания не найдена — проверьте профиль на платформе.")
            return
        kind = "личный профиль" if company.kind == "personal" else "корпоративный профиль"
        await message.answer(f"Компания «{company.name}» · {kind}\nКоманда /digest — кандидаты.")


# --- /pipeline --------------------------------------------------------------------------


@router.message(Command("pipeline"))
async def on_pipeline(message: Message, session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        link = await tg_service.find_link(session, message.chat.id)
        if link is None or link.role != "employer":
            await message.answer("Воронка доступна работодателю. " + NOT_LINKED_TEXT)
            return
        company = await _company_for_link(session, link)
        if company is None:
            await message.answer("Компания не найдена — проверьте профиль на платформе.")
            return
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
        if not needs:
            await message.answer("Активных потребностей нет — создайте их на платформе.")
            return
        lines = [f"Воронка найма «{company.name}»:"]
        for need in needs:
            entries = await matching_service.get_pipeline_entries(session, need.id)
            counts: dict[str, int] = {}
            for entry in entries:
                counts[entry.stage] = counts.get(entry.stage, 0) + 1
            summary = (
                " · ".join(
                    f"{digest_module.STAGE_TITLES.get(stage, stage)}: {counts[stage]}"
                    for stage in matching_service.PIPELINE_STAGE_ORDER
                    if counts.get(stage)
                )
                or "пусто"
            )
            lines.append(f"— «{need.title}»: {summary}")
        await message.answer("\n".join(lines))


# --- callback: кандидат принимает/отклоняет приглашение ---------------------------------


async def _candidate_user(session: AsyncSession, chat_id: int) -> AppUser | None:
    link = await tg_service.find_link(session, chat_id)
    if link is None or link.role != "candidate" or link.user_id is None:
        return None
    return await session.get(AppUser, link.user_id)


@router.callback_query(F.data.startswith("a:"))
async def on_invitation_accept(
    callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    invitation_id = tg_service.unpack_uuid((callback.data or "")[2:])
    chat_id = callback.message.chat.id if callback.message else 0
    async with session_factory() as session:
        user = await _candidate_user(session, chat_id)
        if user is None:
            await callback.answer("Чат не привязан к профилю кандидата", show_alert=True)
            return
        try:
            invitation, company = await matching_service.accept_invitation(
                session, user, invitation_id
            )
        except ApiError as error:
            await callback.answer(error.message, show_alert=True)
            return
        contacts = [f"Приглашение «{invitation.title}» принято!", f"Компания: {company.name}"]
        journal = list(contacts)
        if invitation.contact_method:
            contacts.append(f"Как связаться: {invitation.contact_method}")
            journal.append("Как связаться: указано в приглашении")
        if company.contact_email:
            contacts.append(f"Email: {company.contact_email}")
            journal.append(f"Email: {mask_email(company.contact_email)}")
        if company.contact_phone:
            contacts.append(f"Телефон: {company.contact_phone}")
            journal.append(f"Телефон: {mask_phone(company.contact_phone)}")
        contacts.append("Работодатель увидит ваши контакты на платформе.")
        journal.append("Работодатель увидит ваши контакты на платформе.")
        await tg_service.deliver(
            session,
            None,
            tg_service.OutgoingMessage(
                tg_chat_id=chat_id,
                text="\n".join(contacts),
                kind=tg_service.KIND_REPLY,
                ref_id=invitation.id,
                journal_text="\n".join(journal),
            ),
            allow_contacts=True,
        )
    await callback.answer("Приглашение принято")
    if callback.message:
        await callback.message.answer("\n".join(contacts))


@router.callback_query(F.data.startswith("d:"))
async def on_invitation_decline(
    callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    invitation_id = tg_service.unpack_uuid((callback.data or "")[2:])
    chat_id = callback.message.chat.id if callback.message else 0
    async with session_factory() as session:
        user = await _candidate_user(session, chat_id)
        if user is None:
            await callback.answer("Чат не привязан к профилю кандидата", show_alert=True)
            return
        try:
            await matching_service.decline_invitation(
                session, user, invitation_id, reason_code="other", text="Отклонено через Telegram"
            )
        except ApiError as error:
            await callback.answer(error.message, show_alert=True)
            return
    await callback.answer("Приглашение отклонено")
    if callback.message:
        await callback.message.answer(
            "Приглашение отклонено. Работодатель увидит отказ без деталей."
        )


# --- callback: работодатель двигает кандидата -------------------------------------------


async def _employer_context(session: AsyncSession, chat_id: int) -> tuple[Company, AppUser] | None:
    """Компания и действующий пользователь для callback работодателя.

    Для «HR через Telegram» (без аккаунта) действующим лицом аудита выступает владелец
    компании — сам HR в `app_user` отсутствует."""
    link = await tg_service.find_link(session, chat_id)
    if link is None or link.role != "employer":
        return None
    company = await _company_for_link(session, link)
    if company is None:
        return None
    if link.user_id is not None:
        actor = await session.get(AppUser, link.user_id)
    else:
        actor = await session.get(AppUser, company.user_id)
    if actor is None:
        return None
    return company, actor


@router.callback_query(F.data.startswith("s:"))
async def on_shortlist(
    callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    parts = (callback.data or "").split(":")
    need_id, candidate_id = tg_service.unpack_uuid(parts[1]), tg_service.unpack_uuid(parts[2])
    chat_id = callback.message.chat.id if callback.message else 0
    async with session_factory() as session:
        context = await _employer_context(session, chat_id)
        if context is None:
            await callback.answer("Чат не привязан к компании", show_alert=True)
            return
        company, actor = context
        try:
            need = await employers_service.get_company_need(session, company.id, need_id)
            await matching_service.add_to_pipeline(
                session, need, actor, candidate_id, note="Добавлен из Telegram"
            )
        except ApiError as error:
            await callback.answer(error.message, show_alert=True)
            return
    await callback.answer("Кандидат в шорт-листе")


@router.callback_query(F.data.startswith("i:"))
async def on_invite(
    callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    parts = (callback.data or "").split(":")
    need_id, candidate_id = tg_service.unpack_uuid(parts[1]), tg_service.unpack_uuid(parts[2])
    chat_id = callback.message.chat.id if callback.message else 0
    async with session_factory() as session:
        context = await _employer_context(session, chat_id)
        if context is None:
            await callback.answer("Чат не привязан к компании", show_alert=True)
            return
        company, actor = context
        try:
            need = await employers_service.get_company_need(session, company.id, need_id)
            if not need.salary_from or not need.salary_to:
                await callback.answer(
                    "У потребности не задана зарплата «от–до» — пригласите с платформы",
                    show_alert=True,
                )
                return
            invitation, below_floor = await matching_service.create_invitation(
                session,
                company,
                actor,
                candidate_id=candidate_id,
                need=need,
                title=need.title,
                message=None,
                salary_from=need.salary_from,
                salary_to=need.salary_to,
                contact_method=None,
            )
        except ApiError as error:
            await callback.answer(error.message, show_alert=True)
            return
    note = " (ниже планки кандидата — возможен отказ)" if below_floor else ""
    await callback.answer(f"Приглашение отправлено{note}")


@router.message()
async def on_unknown(message: Message) -> None:
    await message.answer(HELP_TEXT)
