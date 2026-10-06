"""Планировщик бота: недельный дайджест и пуши о новых приглашениях.

Работает внутри процесса бота (`python -m app.tools.bot`) простым asyncio-циклом —
без Celery/cron (решение PLAN.md §7.1: фоновые задачи минимальны). Дедупликация —
по журналу `tg_outbox`, поэтому переживает рестарты:
- недельный дайджест: одна запись `digest_weekly` на чат в ISO-неделю;
- пуш о приглашении: одна запись `invitation_push` на (чат, приглашение).

`POST /internal/digest/run` вызывает `run_digest_for_all` немедленно (демо «прислать
сейчас всем привязанным») — из процесса API, тем же кодом.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.candidate import CandidateProfile
from app.models.employer import Company, Invitation
from app.models.tgbot import TgLink
from app.modules.tgbot import digest as digest_module
from app.modules.tgbot import service as tg_service
from app.modules.tgbot.config import TgBotSettings
from app.modules.tgbot.handlers import _company_for_link

log = logging.getLogger("app.tgbot.scheduler")

# пуши смотрим не дальше, чем на сутки назад (старое добьёт недельный дайджест)
PUSH_LOOKBACK_HOURS = 24


def week_start(now: datetime) -> datetime:
    """Начало текущей ISO-недели (понедельник 00:00 UTC) — окно дедупликации дайджеста."""
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


async def digest_text_for_link(session: AsyncSession, link: TgLink) -> str | None:
    """Текст дайджеста для привязки; None — субъект не найден (дайджест пропускается)."""
    if link.role == "candidate" and link.user_id is not None:
        profile = (
            await session.execute(
                select(CandidateProfile).where(CandidateProfile.user_id == link.user_id)
            )
        ).scalar_one_or_none()
        if profile is None:
            return None
        return await digest_module.build_digest_for_link(session, "candidate", profile)
    company = await _company_for_link(session, link)
    if company is None:
        return None
    return await digest_module.build_digest_for_link(session, "employer", company)


async def run_digest_for_all(
    session: AsyncSession,
    transport: tg_service.BotTransport | None,
    *,
    kind: str = tg_service.KIND_MANUAL_DIGEST,
    dedupe_since: datetime | None = None,
) -> int:
    """Дайджест всем привязанным чатам; возвращает число отправленных сообщений."""
    links = (await session.execute(select(TgLink).order_by(TgLink.created_at))).scalars().all()
    sent = 0
    for link in links:
        if dedupe_since is not None and await tg_service.already_sent(
            session, link.tg_chat_id, kind, since=dedupe_since
        ):
            continue
        text = await digest_text_for_link(session, link)
        if text is None:
            continue
        await tg_service.deliver(
            session,
            transport,
            tg_service.OutgoingMessage(tg_chat_id=link.tg_chat_id, text=text, kind=kind),
        )
        sent += 1
    return sent


async def push_new_invitations(
    session: AsyncSession, transport: tg_service.BotTransport | None
) -> int:
    """Пуш кандидатам о новых приглашениях (кнопки «Принять»/«Отклонить»).

    Порядок запросов: приглашения → профили кандидатов → привязки; дедупликация —
    `invitation_push` + ref_id в `tg_outbox`.
    """
    since = datetime.now(UTC) - timedelta(hours=PUSH_LOOKBACK_HOURS)
    invitations = (
        (
            await session.execute(
                select(Invitation)
                .where(Invitation.status == "sent", Invitation.sent_at >= since)
                .order_by(Invitation.sent_at)
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    if not invitations:
        return 0
    profiles = (
        (
            await session.execute(
                select(CandidateProfile).where(
                    CandidateProfile.id.in_({i.candidate_id for i in invitations})
                )
            )
        )
        .scalars()
        .all()
    )
    user_by_candidate = {p.id: p.user_id for p in profiles}
    links = (
        (
            await session.execute(
                select(TgLink).where(
                    TgLink.role == "candidate",
                    TgLink.user_id.in_(set(user_by_candidate.values())),
                )
            )
        )
        .scalars()
        .all()
    )
    link_by_user = {link.user_id: link for link in links}
    sent = 0
    for invitation in invitations:
        user_id = user_by_candidate.get(invitation.candidate_id)
        link = link_by_user.get(user_id) if user_id else None
        if link is None:
            continue
        if await tg_service.already_sent(
            session, link.tg_chat_id, tg_service.KIND_INVITATION_PUSH, ref_id=invitation.id
        ):
            continue
        company = await session.get(Company, invitation.company_id)
        company_name = company.name if company else "компания"
        await tg_service.deliver(
            session,
            transport,
            tg_service.OutgoingMessage(
                tg_chat_id=link.tg_chat_id,
                text=digest_module.invitation_push_text(company_name, invitation),
                kind=tg_service.KIND_INVITATION_PUSH,
                ref_id=invitation.id,
                buttons=digest_module.invitation_buttons(invitation.id),
            ),
        )
        sent += 1
    return sent


async def scheduler_loop(
    session_factory: async_sessionmaker[AsyncSession],
    transport: tg_service.BotTransport | None,
    settings: TgBotSettings,
) -> None:
    """Вечный цикл: каждый тик — пуши о приглашениях; в настроенный день/час —
    недельный дайджест (дедупликация не даст прислать его дважды за неделю)."""
    log.info(
        "планировщик бота запущен: дайджест по будням weekday=%d hour=%d UTC, тик %d с",
        settings.digest_cron_weekday,
        settings.digest_cron_hour,
        settings.tg_notify_interval_seconds,
    )
    while True:
        try:
            async with session_factory() as session:
                pushed = await push_new_invitations(session, transport)
                if pushed:
                    log.info("пушей о приглашениях отправлено: %d", pushed)
                now = datetime.now(UTC)
                if (
                    now.weekday() == settings.digest_cron_weekday
                    and now.hour == settings.digest_cron_hour
                ):
                    sent = await run_digest_for_all(
                        session,
                        transport,
                        kind=tg_service.KIND_WEEKLY_DIGEST,
                        dedupe_since=week_start(now),
                    )
                    if sent:
                        log.info("недельный дайджест отправлен: %d чатов", sent)
        except Exception:  # сбой одного тика не останавливает планировщик
            log.exception("тик планировщика бота упал")
        await asyncio.sleep(settings.tg_notify_interval_seconds)
