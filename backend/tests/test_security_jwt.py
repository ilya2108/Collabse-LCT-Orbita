"""`core.security` без Keycloak и PostgreSQL: проверка JWT на локально сгенерированном
RSA-ключе (iss, aud, exp/leeway, alg=none/HS256, неизвестный kid), троттлинг JWKS и
`sync_app_user` на FakeSession (удалённый аккаунт, admin только из токена, усыновление
сид-строк, гонка первого входа)."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import security
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import (
    ROLE_ADMIN,
    SEED_SUB_PREFIX,
    JwksCache,
    TokenClaims,
    decode_token,
    get_current_user,
    persisted_roles,
    require_roles,
    sync_app_user,
)
from app.models.user import AppUser
from app.modules.auth import service as auth_service
from tests.conftest import make_user
from tests.fake_session import FakeSession, integrity_error

KID = "test-kid-1"


class LocalJwks(JwksCache):
    """JWKS из памяти: считает обращения и умеет «падать», как недоступный Keycloak."""

    def __init__(self, payload: Any, *, fail: Exception | None = None) -> None:
        super().__init__("http://keycloak.test/certs", ttl_seconds=600, min_interval=30.0)
        self.payload = payload
        self.fail = fail
        self.fetches = 0

    async def _fetch(self) -> Any:
        self.fetches += 1
        if self.fail is not None:
            raise self.fail
        return self.payload


@pytest.fixture()
def rsa_keys() -> tuple[bytes, bytes, dict]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return private_pem, public_pem, {"keys": [jwk]}


@pytest.fixture()
def jwks(rsa_keys, monkeypatch: pytest.MonkeyPatch) -> LocalJwks:
    cache = LocalJwks(rsa_keys[2])
    monkeypatch.setattr(security, "_jwks_cache", cache)
    return cache


def _payload(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base = {
        "sub": "11111111-1111-1111-1111-111111111111",
        "iss": get_settings().keycloak_issuer,
        "aud": "account",
        "azp": "fsp-frontend",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "preferred_username": "anna",
        "email": "Anna@Example.com",
        "email_verified": True,
        "realm_access": {"roles": ["candidate", "offline_access"]},
    }
    base.update(overrides)
    return base


def _token(private_pem: bytes, kid: str = KID, **overrides: Any) -> str:
    return jwt.encode(_payload(**overrides), private_pem, algorithm="RS256", headers={"kid": kid})


# --- decode_token ----------------------------------------------------------------------------


async def test_valid_token_decodes(jwks: LocalJwks, rsa_keys) -> None:
    claims = await decode_token(_token(rsa_keys[0]))
    assert claims.sub == "11111111-1111-1111-1111-111111111111"
    assert claims.roles == ("candidate",) and claims.email == "Anna@Example.com"
    assert claims.email_verified is True and claims.username == "anna"
    assert jwks.fetches == 1
    await decode_token(_token(rsa_keys[0]))
    assert jwks.fetches == 1  # ключ в кэше


async def test_wrong_issuer_rejected(jwks: LocalJwks, rsa_keys) -> None:
    with pytest.raises(ApiError) as exc:
        await decode_token(_token(rsa_keys[0], iss="http://evil.example/realms/fsp"))
    assert exc.value.status_code == 401


async def test_wrong_audience_rejected(jwks: LocalJwks, rsa_keys) -> None:
    with pytest.raises(ApiError) as exc:
        await decode_token(_token(rsa_keys[0], aud="other-client", azp="other-client"))
    assert exc.value.status_code == 401 and "другого приложения" in exc.value.message


async def test_expired_token_and_leeway(jwks: LocalJwks, rsa_keys) -> None:
    now = datetime.now(UTC)
    with pytest.raises(ApiError) as exc:
        await decode_token(_token(rsa_keys[0], exp=int((now - timedelta(minutes=5)).timestamp())))
    assert "истёк" in exc.value.message
    # дрейф часов в пределах leeway (30 с): exp 10 с назад и iat 20 с в будущем проходят
    await decode_token(_token(rsa_keys[0], exp=int((now - timedelta(seconds=10)).timestamp())))
    await decode_token(_token(rsa_keys[0], iat=int((now + timedelta(seconds=20)).timestamp())))
    with pytest.raises(ApiError):
        await decode_token(_token(rsa_keys[0], nbf=int((now + timedelta(minutes=5)).timestamp())))


async def test_alg_none_and_hs256_rejected(jwks: LocalJwks, rsa_keys) -> None:
    unsigned = jwt.encode(_payload(), "", algorithm="none", headers={"kid": KID})
    with pytest.raises(ApiError) as exc:
        await decode_token(unsigned)
    assert exc.value.status_code == 401
    hmac_token = jwt.encode(_payload(), "shared-secret", algorithm="HS256", headers={"kid": KID})
    with pytest.raises(ApiError):
        await decode_token(hmac_token)
    with pytest.raises(ApiError):
        await decode_token("not.a.jwt")
    with pytest.raises(ApiError):  # без kid
        await decode_token(jwt.encode(_payload(), rsa_keys[0], algorithm="RS256"))


async def test_unknown_kid_is_throttled(jwks: LocalJwks, rsa_keys) -> None:
    """Поток токенов со случайными kid не порождает поток запросов к Keycloak."""
    await decode_token(_token(rsa_keys[0]))
    for index in range(50):
        with pytest.raises(ApiError) as exc:
            await decode_token(_token(rsa_keys[0], kid=f"random-{index}"))
        assert "неизвестным ключом" in exc.value.message
    assert jwks.fetches <= 2
    # известный kid по-прежнему работает без обращений к Keycloak
    await decode_token(_token(rsa_keys[0]))
    assert jwks.fetches <= 2


async def test_keycloak_down_keeps_known_keys(rsa_keys, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = LocalJwks(rsa_keys[2])
    monkeypatch.setattr(security, "_jwks_cache", cache)
    await decode_token(_token(rsa_keys[0]))
    # Keycloak «упал», TTL истёк: известный ключ продолжает работать, неизвестный — 401
    cache.fail = httpx.ConnectError("connection refused")
    cache._fetched_at = time.monotonic() - 10_000
    cache._last_attempt = None
    await decode_token(_token(rsa_keys[0]))
    with pytest.raises(ApiError) as exc:
        await decode_token(_token(rsa_keys[0], kid="other"))
    assert "неизвестным ключом" in exc.value.message
    # второй подряд запрос не ждёт Keycloak снова (троттлинг), обращений было ровно 2
    await decode_token(_token(rsa_keys[0]))
    assert cache.fetches == 2


async def test_keycloak_down_without_cache_is_401(
    rsa_keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = LocalJwks(rsa_keys[2], fail=httpx.ConnectError("refused"))
    monkeypatch.setattr(security, "_jwks_cache", cache)
    with pytest.raises(ApiError) as exc:
        await decode_token(_token(rsa_keys[0]))
    assert exc.value.status_code == 401 and "недоступен" in exc.value.message
    # не-JSON ответ Keycloak — тоже 401, а не 500
    broken = LocalJwks(["not", "an", "object"])
    monkeypatch.setattr(security, "_jwks_cache", broken)
    with pytest.raises(ApiError) as exc:
        await decode_token(_token(rsa_keys[0]))
    assert exc.value.status_code == 401


# --- sync_app_user на FakeSession ---------------------------------------------------------------


def _claims(**overrides: Any) -> TokenClaims:
    base: dict[str, Any] = {
        "sub": "sub-" + uuid.uuid4().hex[:8],
        "username": "anna",
        "email": "anna@example.com",
        "email_verified": True,
        "roles": ("candidate",),
    }
    base.update(overrides)
    return TokenClaims(**base)


async def test_deleted_account_is_not_repopulated() -> None:
    """После DELETE /auth/account повторный вход не возвращает email/username в БД."""
    user = make_user(roles=["candidate"], email="anna@example.com", consent=True)
    user.email_hash = "h" * 64
    session = FakeSession(scalars=[None, None])  # профиля и компании нет
    await auth_service.delete_account(session, user, ip=None)
    assert user.email is None and user.username.startswith("deleted-")

    claims = _claims(sub=user.keycloak_sub, roles=("candidate", "admin"))
    session = FakeSession(scalars=[user])
    synced = await sync_app_user(session, claims)
    assert synced is user
    assert user.email is None and user.email_hash is None
    assert user.username.startswith("deleted-") and user.deleted_at is not None
    assert user.effective_roles == ["candidate"] and session.commits == 0
    with pytest.raises(ApiError) as exc:
        await get_current_user(claims, FakeSession(scalars=[user]))
    assert exc.value.status_code == 403


async def test_admin_role_lives_only_in_token() -> None:
    user = make_user(roles=["candidate"], email="anna@example.com")
    claims = _claims(sub=user.keycloak_sub, roles=("admin", "candidate"))
    session = FakeSession(scalars=[user])
    await sync_app_user(session, claims)
    assert user.roles == ["candidate"] and user.effective_roles == ["admin", "candidate"]
    assert user.has_role(ROLE_ADMIN)
    admin_only = require_roles(ROLE_ADMIN)
    assert await admin_only(user) is user

    # роль отозвали в Keycloak: следующий токен без admin — доступа больше нет
    await sync_app_user(FakeSession(scalars=[user]), _claims(sub=user.keycloak_sub))
    assert user.effective_roles == ["candidate"] and not user.has_role(ROLE_ADMIN)
    with pytest.raises(ApiError) as exc:
        await admin_only(user)
    assert exc.value.status_code == 403


async def test_legacy_admin_in_db_is_cleaned() -> None:
    user = make_user(roles=["candidate"])
    user.roles = ["admin", "candidate"]  # строка со времён, когда admin персистился
    await sync_app_user(FakeSession(scalars=[user]), _claims(sub=user.keycloak_sub))
    assert user.roles == ["candidate"] and user.effective_roles == ["candidate"]
    assert persisted_roles({"admin", "employer", "nope"}) == ["employer"]


async def test_choose_role_as_admin_does_not_persist_admin() -> None:
    user = make_user(roles=["admin", "candidate"], active_role="candidate")
    assert user.roles == ["candidate"] and user.token_roles == ("admin",)
    await auth_service.choose_role(FakeSession(), user, "employer", ip=None)
    assert user.roles == ["candidate", "employer"]
    assert user.effective_roles == ["admin", "candidate", "employer"]
    assert auth_service.build_me(user).roles == ["admin", "candidate", "employer"]


async def test_first_login_inserts_user_with_hashed_email() -> None:
    claims = _claims(roles=("candidate",))
    session = FakeSession(scalars=[None, []])  # по sub не найден, сид-строк нет
    user = await sync_app_user(session, claims)
    assert isinstance(user, AppUser) and user.keycloak_sub == claims.sub
    assert user.email == "anna@example.com" and len(user.email_hash or "") == 64
    assert user.roles == ["candidate"] and user.active_role == "candidate"
    assert user.effective_roles == ["candidate"] and session.commits == 1
    assert len(session.added_of(AppUser)) == 1


async def test_unverified_email_skips_adoption() -> None:
    claims = _claims(email_verified=False)
    session = FakeSession(scalars=[None])  # запроса по email_hash быть не должно
    user = await sync_app_user(session, claims)
    assert user.keycloak_sub == claims.sub and len(session.statements) == 1


async def test_seed_row_is_adopted_once() -> None:
    seed = make_user(roles=["employer"], email="anna@example.com", consent=False)
    seed.keycloak_sub = f"{SEED_SUB_PREFIX}anna"
    claims = _claims()
    session = FakeSession(scalars=[None, [seed]])
    user = await sync_app_user(session, claims)
    assert user is seed and seed.keycloak_sub == claims.sub
    assert session.added == [] and seed.effective_roles == ["candidate", "employer"]
    # два кандидата на усыновление — не усыновляем, создаём новую строку
    other = make_user(roles=[], email="anna@example.com")
    other.keycloak_sub = f"{SEED_SUB_PREFIX}dup"
    session = FakeSession(scalars=[None, [seed, other]])
    created = await sync_app_user(session, _claims())
    assert created is not seed and created is not other and len(session.added) == 1


async def test_first_login_race_rereads_row() -> None:
    """Два параллельных первых входа: второй INSERT получает IntegrityError и перечитывает
    строку, вставленную первым, вместо 500."""
    claims = _claims()
    existing = make_user(roles=["candidate"], email="anna@example.com")
    existing.keycloak_sub = claims.sub
    session = FakeSession(
        scalars=[None, [], existing],
        flush_errors=[integrity_error("uq_app_user_keycloak_sub")],
    )
    user = await sync_app_user(session, claims)
    assert user is existing and session.added == [] and session.commits == 1
