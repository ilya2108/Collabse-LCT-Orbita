"""Аутентификация и авторизация.

- Люди: JWT Keycloak (OIDC + PKCE на фронте, realm `fsp`), валидация подписи по JWKS
  realm'а (кэш 10 минут; форс-обновление при неизвестном kid — не чаще раза в
  JWKS_REFRESH_MIN_INTERVAL секунд и под замком, чтобы поток токенов со случайными kid
  не превращался в поток запросов к Keycloak).
- Роли `candidate` / `employer` живут в БД (`app_user.roles`); Keycloak только
  аутентифицирует. При первом входе ролей нет — `GET /auth/me` отдаёт `roles: []`, фронт
  ведёт на экран выбора роли. Роли из `realm_access.roles` токена объединяются с БД:
  candidate/employer дописываются в строку, а `admin` НИКОГДА не персистится — он
  действует только пока есть в токене (`AppUser.token_roles` → `effective_roles`), поэтому
  отзыв роли в Keycloak вступает в силу со следующего запроса.
- Удалённый аккаунт (`deleted_at`) не обновляется из токена: ПДн, стёртые по 152-ФЗ,
  не возвращаются в БД при повторном входе; `get_current_user` отдаёт 403.
- Джобы, импорт и заглушка реестра ФСП: статический `X-Internal-Token`
  (constant-time сравнение).

Двойное имя Keycloak: JWKS качаем по внутреннему URL (KEYCLOAK_INTERNAL_URL), `iss`
проверяем по внешнему (KEYCLOAK_ISSUER).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hmac import compare_digest
from typing import Annotated

import httpx
import jwt
from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import ApiError, forbidden, unauthorized
from app.core.pii import get_pii_crypto
from app.models.user import AppUser

ROLE_CANDIDATE = "candidate"
ROLE_EMPLOYER = "employer"
ROLE_ADMIN = "admin"
# роли, которые пользователь выбирает сам при первом входе (POST /auth/role)
SELECTABLE_ROLES = (ROLE_CANDIDATE, ROLE_EMPLOYER)
# канонический список ролей проекта; прочие роли realm игнорируются
KNOWN_ROLES = frozenset((ROLE_CANDIDATE, ROLE_EMPLOYER, ROLE_ADMIN))
# роли, которые хранятся в app_user.roles (admin — только из токена)
PERSISTED_ROLES = frozenset((ROLE_CANDIDATE, ROLE_EMPLOYER))
# строки app_user, созданные сидом до первого входа: keycloak_sub = "seed:<что-угодно>";
# только такие строки «усыновляются» по email_hash при первом входе
SEED_SUB_PREFIX = "seed:"
# допуск на рассинхрон часов между Keycloak и backend (iat/nbf/exp), секунды
JWT_LEEWAY_SECONDS = 30
# минимальная пауза между обращениями к JWKS Keycloak (неизвестный kid, истёкший TTL), секунды
JWKS_REFRESH_MIN_INTERVAL = 30.0

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class TokenClaims:
    """Проверенные claims access-токена Keycloak."""

    sub: str
    username: str
    email: str | None
    email_verified: bool
    roles: tuple[str, ...]
    azp: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    def has_any_role(self, roles: tuple[str, ...] | frozenset[str]) -> bool:
        return bool(set(self.roles) & set(roles))


class JwksCache:
    """JWKS realm'а с TTL-кэшем в памяти процесса.

    Обновление: по истечении TTL или при неизвестном kid, но не чаще `min_interval`
    секунд с последней попытки (успешной или нет) и только одним запросом за раз
    (asyncio.Lock). При недоступном Keycloak известные ключи продолжают работать.
    """

    def __init__(
        self, url: str, ttl_seconds: int, min_interval: float = JWKS_REFRESH_MIN_INTERVAL
    ) -> None:
        self._url = url
        self._ttl = ttl_seconds
        self._min_interval = min_interval
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._last_attempt: float | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    def _get_lock(self) -> asyncio.Lock:
        # Lock привязан к циклу событий; тесты создают новый цикл на каждый тест
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def _fetch(self) -> dict:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(self._url)
            response.raise_for_status()
            return response.json()

    async def _refresh(self) -> None:
        self._last_attempt = time.monotonic()
        payload = await self._fetch()
        if not isinstance(payload, dict):
            raise ValueError("JWKS: ожидался JSON-объект")
        keys: dict[str, jwt.PyJWK] = {}
        for jwk in payload.get("keys", []) or []:
            if not isinstance(jwk, dict) or jwk.get("use") not in (None, "sig"):
                continue
            try:
                key = jwt.PyJWK(jwk)
            except jwt.PyJWKError:
                continue
            if key.key_id:
                keys[key.key_id] = key
        self._keys = keys
        self._fetched_at = time.monotonic()

    def _needs_refresh(self, kid: str) -> bool:
        now = time.monotonic()
        stale = self._fetched_at is None or now - self._fetched_at > self._ttl
        if not stale and kid in self._keys:
            return False
        throttled = self._last_attempt is not None and now - self._last_attempt < self._min_interval
        return not throttled

    async def get_key(self, kid: str) -> jwt.PyJWK:
        if self._needs_refresh(kid):
            async with self._get_lock():
                # соседний запрос мог обновить ключи, пока мы ждали замок
                if self._needs_refresh(kid):
                    try:
                        await self._refresh()
                    except (httpx.HTTPError, ValueError) as exc:  # Keycloak недоступен / не JSON
                        if not self._keys:
                            raise unauthorized(
                                "Сервис авторизации недоступен, повторите позже"
                            ) from exc
        key = self._keys.get(kid)
        if key is None:
            raise unauthorized("Токен подписан неизвестным ключом")
        return key


_jwks_cache: JwksCache | None = None


def get_jwks_cache() -> JwksCache:
    global _jwks_cache
    if _jwks_cache is None:
        settings = get_settings()
        _jwks_cache = JwksCache(settings.jwks_url, settings.jwks_cache_ttl_seconds)
    return _jwks_cache


def reset_jwks_cache() -> None:
    global _jwks_cache
    _jwks_cache = None


def parse_token_payload(payload: dict) -> TokenClaims:
    """Достаёт из payload токена канонические claims (роли фильтруются по KNOWN_ROLES)."""
    realm_roles = payload.get("realm_access", {}).get("roles", []) or []
    roles = tuple(sorted(set(realm_roles) & KNOWN_ROLES))
    return TokenClaims(
        sub=payload["sub"],
        username=payload.get("preferred_username") or payload["sub"],
        email=payload.get("email"),
        email_verified=bool(payload.get("email_verified", False)),
        roles=roles,
        azp=payload.get("azp"),
        raw=payload,
    )


def validate_audience(payload: dict, allowed: list[str]) -> bool:
    """`aud` (или `azp`) должен пересекаться со списком допустимых client id.
    Пустой список допустимых — fail-closed: ни один токен не проходит."""
    if not allowed:
        return False
    aud = payload.get("aud")
    audiences = {aud} if isinstance(aud, str) else set(aud or [])
    if payload.get("azp"):
        audiences.add(payload["azp"])
    return bool(audiences & set(allowed))


async def decode_token(token: str) -> TokenClaims:
    settings = get_settings()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise unauthorized("Некорректный токен авторизации") from exc
    kid = header.get("kid")
    if not kid or not isinstance(kid, str):
        raise unauthorized("Некорректный токен авторизации")
    key = await get_jwks_cache().get_key(kid)
    try:
        payload = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=settings.keycloak_issuer,
            leeway=JWT_LEEWAY_SECONDS,
            options={"verify_aud": False, "require": ["exp", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise unauthorized("Срок действия токена истёк") from exc
    except jwt.InvalidTokenError as exc:
        raise unauthorized("Некорректный токен авторизации") from exc
    if not validate_audience(payload, settings.keycloak_audience_list):
        raise unauthorized("Токен выпущен для другого приложения")
    return parse_token_payload(payload)


async def get_token_claims(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> TokenClaims:
    if credentials is None or not credentials.credentials:
        raise unauthorized()
    return await decode_token(credentials.credentials)


def merge_roles(db_roles: list[str] | tuple[str, ...], token_roles: tuple[str, ...]) -> list[str]:
    """Эффективные роли = роли из БД ∪ известные роли токена (чистая функция для тестов)."""
    return sorted((set(db_roles) | set(token_roles)) & KNOWN_ROLES)


def persisted_roles(roles: list[str] | tuple[str, ...] | set[str]) -> list[str]:
    """Роли, которые хранятся в `app_user.roles`: только candidate/employer (без admin)."""
    return sorted(set(roles) & PERSISTED_ROLES)


def default_active_role(roles: list[str] | tuple[str, ...]) -> str | None:
    """Активная роль по умолчанию: первая из candidate/employer, присутствующая в ролях."""
    for role in SELECTABLE_ROLES:
        if role in roles:
            return role
    return None


async def _find_by_sub(session: AsyncSession, sub: str) -> AppUser | None:
    return (
        await session.execute(select(AppUser).where(AppUser.keycloak_sub == sub))
    ).scalar_one_or_none()


async def _adopt_seed_user(session: AsyncSession, email_hash: str, sub: str) -> AppUser | None:
    """«Усыновление» строки, созданной сидом до первого входа: только непривязанная
    (`keycloak_sub` с префиксом `seed:`), не удалённая, ровно одна. Строку с реальным
    sub перепривязать нельзя — иначе чужой аккаунт с тем же email получил бы её роли и ПДн."""
    rows = (
        (
            await session.execute(
                select(AppUser).where(
                    AppUser.email_hash == email_hash,
                    AppUser.keycloak_sub.like(f"{SEED_SUB_PREFIX}%"),
                    AppUser.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if len(rows) != 1:
        return None
    user = rows[0]
    user.keycloak_sub = sub
    return user


async def _insert_user(
    session: AsyncSession, claims: TokenClaims, email_hash: str | None
) -> AppUser:
    """Первый вход: INSERT в savepoint; параллельный первый вход того же пользователя
    (StrictMode шлёт два /auth/me) даёт IntegrityError по keycloak_sub — перечитываем строку."""
    roles = persisted_roles(claims.roles)
    user = AppUser(
        keycloak_sub=claims.sub,
        username=claims.username,
        email=claims.email,
        email_hash=email_hash,
        email_verified=claims.email_verified,
        roles=roles,
        active_role=default_active_role(roles),
    )
    try:
        async with session.begin_nested():
            session.add(user)
            await session.flush()
    except IntegrityError:
        existing = await _find_by_sub(session, claims.sub)
        if existing is None:
            raise
        return existing
    return user


async def sync_app_user(session: AsyncSession, claims: TokenClaims) -> AppUser:
    """Upsert зеркала `app_user` по `keycloak_sub`.

    Email хранится зашифрованным (email_enc) + blind index (email_hash); candidate/employer
    из токена дописываются в роли БД, `admin` остаётся только в `token_roles`;
    `active_role` выставляется, если пуст и роль одна. Удалённая строка (`deleted_at`)
    не трогается вовсе — ПДн не должны вернуться при повторном входе.
    """
    user = await _find_by_sub(session, claims.sub)
    email_hash = get_pii_crypto().email_hmac(claims.email) if claims.email else None
    if user is None and email_hash is not None and claims.email_verified:
        user = await _adopt_seed_user(session, email_hash, claims.sub)
    if user is None:
        user = await _insert_user(session, claims, email_hash)
    if user.deleted_at is not None:
        # ничего не записываем: ни email/username из токена, ни роли — строка остаётся стёртой
        user.token_roles = ()
        return user

    if user.username != claims.username:
        user.username = claims.username
    if claims.email and user.email != claims.email:
        user.email = claims.email
        user.email_hash = email_hash
    if user.email_verified != claims.email_verified:
        user.email_verified = claims.email_verified
    # роли БД ∪ candidate/employer токена; admin, попавший в БД ранее, вычищается
    stored = persisted_roles(merge_roles(user.roles or [], claims.roles))
    if sorted(user.roles or []) != stored:
        user.roles = stored
    user.token_roles = claims.roles
    if user.active_role is None:
        user.active_role = default_active_role(user.effective_roles)
    await session.flush()
    await session.commit()
    return user


async def get_current_user(
    claims: Annotated[TokenClaims, Depends(get_token_claims)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppUser:
    """Текущий пользователь (зеркалится в app_user). Роли не требуются: пользователь без
    роли должен дойти до `GET /auth/me` и экрана выбора роли."""
    user = await sync_app_user(session, claims)
    if user.deleted_at is not None:
        raise forbidden("Учётная запись удалена по запросу пользователя")
    return user


def check_roles(user_roles: list[str] | tuple[str, ...], allowed: tuple[str, ...]) -> bool:
    """Чистая проверка пересечения ролей (юнит-тестируется отдельно от FastAPI)."""
    return bool(set(user_roles) & set(allowed))


def require_roles(*allowed: str):
    """Зависимость: доступ только ролям из списка (эффективные роли: БД ∪ токен); иначе 403."""

    async def dependency(
        user: Annotated[AppUser, Depends(get_current_user)],
    ) -> AppUser:
        if not check_roles(user.effective_roles, allowed):
            raise forbidden()
        return user

    return dependency


def consent_required(
    message: str = "Требуется согласие на обработку персональных данных",
) -> ApiError:
    """403 `consent_required` — фронт отличает «нет согласия» (экран согласия) от «нет прав»."""
    return ApiError(403, "consent_required", message)


def require_consent(user: AppUser) -> None:
    """Проверка согласия на обработку ПДн (152-ФЗ): без него — 403 consent_required."""
    if user.consent_pdn_at is None:
        raise consent_required()


def utcnow() -> datetime:
    return datetime.now(UTC)


def verify_internal_token(provided: str | None, expected: str) -> bool:
    """Constant-time сравнение X-Internal-Token."""
    if not provided or not expected:
        return False
    return compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


async def require_internal_token(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    settings = get_settings()
    if not verify_internal_token(x_internal_token, settings.internal_api_token):
        raise unauthorized("Неверный внутренний токен")
