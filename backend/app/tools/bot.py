"""Телеграм-бот «ФСП.Орбита» — точка входа (`python -m app.tools.bot`).

Режимы (TELEGRAM_BOT_MODE):
- `stub` (по умолчанию): без сети и токена — polling не запускается, работает только
  планировщик (недельный дайджест и пуши пишутся в таблицу `tg_outbox`); так compose
  и стенд живут без ключей;
- `polling`: реальный бот aiogram (нужен TELEGRAM_BOT_TOKEN от @BotFather) + планировщик.

Бот — часть монолита: ходит в ту же БД через `get_session_factory()` (не через HTTP);
бизнес-логика — сервисы модулей matching/employers, тексты — modules/tgbot/digest.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.db import dispose_engine, get_session_factory
from app.core.pii import get_pii_crypto
from app.modules.tgbot.config import get_tgbot_settings
from app.modules.tgbot.scheduler import scheduler_loop
from app.modules.tgbot.service import AiogramTransport

log = logging.getLogger("app.tgbot.main")


async def run() -> None:
    settings = get_tgbot_settings()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)-5.5s [%(name)s] %(message)s")
    get_pii_crypto()  # fail-closed: без ключей ПДн бот не стартует (как и API)
    session_factory = get_session_factory()

    if settings.telegram_bot_mode == "stub":
        log.info("TELEGRAM_BOT_MODE=stub: без сети, исходящие пишутся в tg_outbox")
        try:
            await scheduler_loop(session_factory, None, settings)
        finally:
            await dispose_engine()
        return

    from aiogram import Bot, Dispatcher

    from app.modules.tgbot.handlers import router as handlers_router

    bot = Bot(token=settings.telegram_bot_token)
    transport = AiogramTransport(bot)
    dispatcher = Dispatcher(session_factory=session_factory)
    dispatcher.include_router(handlers_router)
    scheduler = asyncio.create_task(scheduler_loop(session_factory, transport, settings))
    log.info("TELEGRAM_BOT_MODE=polling: бот запущен")
    try:
        await dispatcher.start_polling(bot)
    finally:
        scheduler.cancel()
        await dispose_engine()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("бот остановлен")


if __name__ == "__main__":
    main()
