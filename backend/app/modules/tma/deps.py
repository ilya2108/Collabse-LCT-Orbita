"""Авторизация Mini App: валидация `initData` Telegram.WebApp и актор запроса.

Каждый запрос мини-аппа несёт заголовок `X-Telegram-Init-Data` (строка
`Telegram.WebApp.initData`). Проверка — по алгоритму Telegram:
`secret = HMAC_SHA256(key="WebAppData", msg=bot_token)`, затем HMAC-SHA256 от
data-check-string (все пары `key=value`, кроме `hash`, отсортированные по ключу
и склеенные `\\n`) сравнивается с полем `hash`; `auth_date` не старше
`TMA_INIT_DATA_TTL_SECONDS`. Из проверенных полей берётся id пользователя
Telegram (в личном чате он равен id чата) → строка `tg_link` по `tg_chat_id` → актор:

- `kind="candidate"` / `kind="employer"` — пользователь платформы (`tg_link.user_id`);
- `kind="member"` — участник компании без аккаунта («HR через Telegram»).

Непривязанный чат → 403 `tma_not_linked`. В `APP_ENV=dev|test` при
`TELEGRAM_BOT_MODE=stub` дополнительно принимается строка `dev:<tg_chat_id>`
без криптографии — исключительно локальная разработка и тесты.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import parse_qsl

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import ApiError, unauthorized
from app.models.employer import CompanyMember
from app.models.user import AppUser
from app.modules.tgbot import service as tg_service
from app.modules.tgbot.config import get_tgbot_settings
from app.modules.tma.config import get_tma_settings

INIT_DATA_HEADER = "X-Telegram-Init-Data"
DEV_PREFIX = "dev:"
NOT_LINKED_MESSAGE = "Сначала привяжите бота: /start с кодом из настроек"


class InitDataError(ValueError):
    """`initData` не прошла проверку (причина — в тексте; наружу уходит 401)."""


def validate_init_data(
    raw: str, bot_token: str, now: datetime, ttl_seconds: int = 3600
) -> dict[str, str]:
    """Проверяет подпись и свежесть `initData`; возвращает раскодированные поля без `hash`.

    Чистая функция (время передаётся параметром): пары разбираются как query-строка,
    data-check-string собирается из пар, отсортированных по имени ключа, — порядок
    ключей в исходной строке значения не имеет.
    """
    fields = dict(parse_qsl(raw, keep_blank_values=True))
    received_hash = fields.pop("hash", "")
    if not received_hash:
        raise InitDataError("нет поля hash")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, received_hash):
        raise InitDataError("подпись не совпадает")
    try:
        auth_date = int(fields["auth_date"])
    except (KeyError, ValueError) as error:
        raise InitDataError("нет корректного auth_date") from error
    if now.timestamp() - auth_date > ttl_seconds:
        raise InitDataError("initData просрочена — перезапустите мини-апп")
    return fields


@dataclass(frozen=True)
class TmaActor:
    """Актор запроса мини-аппа; заполнено ровно одно из полей `user`/`member`."""

    kind: Literal["candidate", "employer", "member"]
    user: AppUser | None = None
    member: CompanyMember | None = None


def _dev_chat_id(raw: str) -> int | None:
    """`dev:<tg_chat_id>` без криптографии — строго `APP_ENV=dev|test` И stub-режим бота."""
    if not raw.startswith(DEV_PREFIX):
        return None
    if get_settings().app_env not in ("dev", "test"):
        return None
    if get_tgbot_settings().telegram_bot_mode != "stub":
        return None
    try:
        return int(raw[len(DEV_PREFIX) :])
    except ValueError:
        return None


def _tg_user_id(raw: str) -> int:
    """Id пользователя Telegram из заголовка: dev-строка или настоящая `initData`."""
    dev_id = _dev_chat_id(raw)
    if dev_id is not None:
        return dev_id
    try:
        fields = validate_init_data(
            raw,
            get_tgbot_settings().telegram_bot_token,
            datetime.now(UTC),
            get_tma_settings().tma_init_data_ttl_seconds,
        )
    except InitDataError as error:
        raise unauthorized(f"initData не прошла проверку: {error}") from error
    try:
        return int(json.loads(fields["user"])["id"])
    except (KeyError, TypeError, ValueError) as error:
        raise unauthorized("В initData нет пользователя Telegram") from error


def _not_linked() -> ApiError:
    return ApiError(403, "tma_not_linked", NOT_LINKED_MESSAGE)


async def get_tma_actor(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> TmaActor:
    """FastAPI-зависимость: проверенный актор мини-аппа (контракт — в докстринге модуля)."""
    raw = request.headers.get(INIT_DATA_HEADER, "")
    if not raw:
        raise unauthorized(f"Запрос мини-аппа должен нести заголовок {INIT_DATA_HEADER}")
    tg_user_id = _tg_user_id(raw)
    link = await tg_service.find_link(session, tg_user_id)
    if link is None:
        raise _not_linked()
    if link.user_id is not None:
        user = await session.get(AppUser, link.user_id)
        if user is None:
            raise _not_linked()
        if link.role == "candidate":
            return TmaActor(kind="candidate", user=user)
        return TmaActor(kind="employer", user=user)
    member = await session.get(CompanyMember, link.member_id)
    if member is None or member.status == "revoked":
        raise _not_linked()
    return TmaActor(kind="member", member=member)
