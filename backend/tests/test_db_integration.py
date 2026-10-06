"""Интеграционные тесты с PostgreSQL (`@pytest.mark.db`): то, что FakeSession не покажет —
гонка первого входа, стирание ПДн после удаления, admin только из токена, усыновление
сид-строк, ротация ключей `rekey`, журнал LLM в чужой транзакции, NOT NULL-списки
профиля, отсутствие параметров SQL в тексте ошибок.

БД — `DATABASE_URL` из tests/conftest.py (`…/fsp_test` на compose-PostgreSQL: `make dev-infra`);
база создаётся при первом запуске, схема — `Base.metadata.create_all`. Без PostgreSQL набор
пропускается (skip), поэтому CI без БД остаётся зелёным.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import pii
from app.core.config import get_settings
from app.core.db import dispose_engine, get_engine, get_session_factory
from app.core.errors import ApiError
from app.core.pii import PiiCrypto, reset_pii_crypto
from app.core.security import SEED_SUB_PREFIX, TokenClaims, get_current_user, sync_app_user
from app.models.base import Base
from app.models.llm import LlmCall
from app.models.user import AppUser
from app.modules.auth import service as auth_service
from app.modules.candidates import service as candidates_service
from app.modules.candidates.schemas import CandidateMeIn
from app.modules.llm.journal import LlmCallRecord, LlmJournal
from app.modules.llm.providers.stub import StubProvider
from app.modules.llm.service import LLMService
from app.tools import rekey
from tests.test_llm_helpers import NEED_VARS, reset_llm_state

pytestmark = pytest.mark.db

# порядок важен: сначала таблицы с FK на app_user
TABLES_TO_CLEAN = ("llm_call", "audit_log", "candidate_profile", "app_user")


async def _ensure_database(url) -> str | None:
    """Создаёт БД из DATABASE_URL, если её нет. None — сервер доступен; иначе причина skip."""
    admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    except Exception as exc:  # noqa: BLE001 — любая причина = окружение без PostgreSQL
        return f"PostgreSQL недоступен: {type(exc).__name__}"
    finally:
        await admin.dispose()
    return None


async def _create_schema() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await dispose_engine()


@pytest.fixture(scope="session")
def pg_schema() -> None:
    url = make_url(get_settings().database_url)
    reason = asyncio.run(_ensure_database(url))
    if reason:
        pytest.skip(reason)
    asyncio.run(_create_schema())


@pytest.fixture()
async def db(pg_schema: None) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Фабрика сессий на чистых таблицах; движок пересоздаётся на каждый тест (свой loop)."""
    factory = get_session_factory()
    async with factory() as session:
        for table in TABLES_TO_CLEAN:
            await session.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
        await session.commit()
    try:
        yield factory
    finally:
        await dispose_engine()


def _claims(
    *,
    sub: str | None = None,
    email: str | None = "anna@example.com",
    roles: tuple[str, ...] = ("candidate",),
    verified: bool = True,
    username: str = "anna",
) -> TokenClaims:
    return TokenClaims(
        sub=sub or f"sub-{uuid.uuid4().hex[:12]}",
        username=username,
        email=email,
        email_verified=verified,
        roles=roles,
    )


async def _user_by_sub(session: AsyncSession, sub: str) -> AppUser:
    return (await session.execute(select(AppUser).where(AppUser.keycloak_sub == sub))).scalar_one()


# --- sync_app_user ------------------------------------------------------------------------------


async def test_first_login_race_creates_single_row(db: async_sessionmaker[AsyncSession]) -> None:
    """React StrictMode шлёт два /auth/me сразу после редиректа: оба должны получить 200."""
    claims = _claims()

    async def login() -> uuid.UUID:
        async with db() as session:
            return (await sync_app_user(session, claims)).id

    ids = await asyncio.gather(login(), login(), login())
    assert len(set(ids)) == 1
    async with db() as session:
        assert await session.scalar(select(func.count(AppUser.id))) == 1


async def test_deleted_account_stays_erased_after_relogin(
    db: async_sessionmaker[AsyncSession],
) -> None:
    claims = _claims()
    async with db() as session:
        user = await sync_app_user(session, claims)
        await auth_service.delete_account(session, user, ip="127.0.0.1")
    # повторный вход с живым токеном: 403 и ни одного восстановленного поля
    async with db() as session:
        with pytest.raises(ApiError) as exc:
            await get_current_user(claims, session)
        assert exc.value.status_code == 403
    async with db() as session:
        row = await _user_by_sub(session, claims.sub)
        assert row.email is None and row.email_hash is None
        assert row.username.startswith("deleted-") and row.deleted_at is not None
        assert row.consent_pdn_at is None and row.email_verified is False


async def test_admin_role_not_persisted_and_revocation_applies(
    db: async_sessionmaker[AsyncSession],
) -> None:
    claims = _claims(roles=("admin", "candidate"))
    async with db() as session:
        user = await sync_app_user(session, claims)
        assert user.effective_roles == ["admin", "candidate"]
    async with db() as session:
        row = await _user_by_sub(session, claims.sub)
        assert row.roles == ["candidate"]  # admin в БД не записан
        revoked = await sync_app_user(session, _claims(sub=claims.sub, roles=("candidate",)))
        assert revoked.effective_roles == ["candidate"] and not revoked.has_role("admin")


async def test_seed_row_adopted_but_real_sub_never_rebound(
    db: async_sessionmaker[AsyncSession],
) -> None:
    crypto = pii.get_pii_crypto()
    async with db() as session:
        seed = AppUser(
            keycloak_sub=f"{SEED_SUB_PREFIX}anna",
            username="seed-anna",
            email="anna@example.com",
            email_hash=crypto.email_hmac("anna@example.com"),
            roles=["employer"],
        )
        alice = AppUser(
            keycloak_sub="real-alice",
            username="alice",
            email="alice@example.com",
            email_hash=crypto.email_hmac("alice@example.com"),
            roles=["candidate"],
        )
        session.add_all([seed, alice])
        await session.commit()
        seed_id, alice_id = seed.id, alice.id
    async with db() as session:
        adopted = await sync_app_user(session, _claims(email="Anna@Example.com"))
        assert adopted.id == seed_id and adopted.roles == ["candidate", "employer"]
    async with db() as session:
        # чужой аккаунт с тем же email (подтверждённым или нет) строку Алисы не получает
        mallory = await sync_app_user(session, _claims(email="alice@example.com", verified=False))
        mallory_verified = await sync_app_user(session, _claims(email="alice@example.com"))
        assert alice_id not in {mallory.id, mallory_verified.id}
        assert (await session.get(AppUser, alice_id)).keycloak_sub == "real-alice"


# --- rekey ---------------------------------------------------------------------------------------


async def test_rekey_reencrypts_rows_with_active_key(
    db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сценарий из docstring rekey: новый ключ ставится первым, прогон, старый удаляется —
    после этого шифртекст обязан читаться только новым ключом."""
    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    hmac_key = get_settings().pii_hmac_key
    monkeypatch.setattr(pii, "_crypto", PiiCrypto([old_key], hmac_key))
    try:
        async with db() as session:
            user = AppUser(
                keycloak_sub="rekey-sub",
                username="rekey",
                email="rekey@example.com",
                email_hash=pii.get_pii_crypto().email_hmac("rekey@example.com"),
            )
            session.add(user)
            await session.commit()
            user_id = user.id
        monkeypatch.setattr(pii, "_crypto", PiiCrypto([new_key, old_key], hmac_key))
        assert await rekey._rekey_table(AppUser, ("email",)) == 1
        async with db() as session:
            # сырой bytea, минуя EncryptedStr (он бы расшифровал MultiFernet'ом)
            raw = await session.scalar(
                text("SELECT email_enc FROM app_user WHERE id = :id"), {"id": user_id}
            )
        assert Fernet(new_key).decrypt(bytes(raw)) == b"rekey@example.com"
        with pytest.raises(InvalidToken):
            Fernet(old_key).decrypt(bytes(raw))
    finally:
        reset_pii_crypto()


# --- журнал LLM в бизнес-транзакции -------------------------------------------------------------


async def test_journal_in_business_transaction_with_uuid_meta(
    db: async_sessionmaker[AsyncSession],
) -> None:
    reset_llm_state()
    journal = LlmJournal()
    service = LLMService(provider=StubProvider(), journal=journal)
    async with db() as session:
        session.add(AppUser(keycloak_sub="biz-sub", username="biz"))  # бизнес-изменение
        result = await service.complete(
            "need_parse",
            NEED_VARS,
            session=session,
            meta={"session_id": uuid.uuid4(), "started_at": datetime.now(UTC)},
        )
        assert result.status == "ok" and journal.db_errors == 0
        # сбой журнала (CHECK status_known) не переводит сессию в rollback-only
        broken = LlmCallRecord(
            kind="need_parse",
            provider="stub",
            model="m",
            prompt_version="v1",
            prompt_hash="h" * 64,
            status="bogus",
        )
        assert await journal.record(session, broken) is None and journal.db_errors == 1
        await session.commit()  # без PendingRollbackError
    async with db() as session:
        rows = (await session.execute(select(LlmCall))).scalars().all()
        assert len(rows) == 1 and rows[0].status == "ok"
        assert isinstance(rows[0].request_json["meta"]["session_id"], str)
        assert (
            await session.scalar(
                select(func.count(AppUser.id)).where(AppUser.keycloak_sub == "biz-sub")
            )
            == 1
        )
    reset_llm_state()


# --- профиль кандидата ---------------------------------------------------------------------------


async def test_update_profile_null_lists_and_sanitized_about(
    db: async_sessionmaker[AsyncSession],
) -> None:
    async with db() as session:
        user = await sync_app_user(session, _claims())
        profile = await candidates_service.get_or_create_profile(session, user)
        changes = CandidateMeIn.model_validate(
            {
                "stack_tags_declared": None,
                "roles_declared": None,
                "soft_skills_declared": None,
                "full_name": "Петров Иван",
                "about": "Иван Петров, звоните +7 916 123-45-67",
            }
        ).changes()
        profile = await candidates_service.update_profile(session, profile, changes)
        profile_id = profile.id
    async with db() as session:
        row = await session.get(candidates_service.CandidateProfile, profile_id)
        assert row.stack_tags_declared == [] and row.roles_declared == []
        assert row.soft_skills_declared == [] and row.full_name == "Петров Иван"
        assert "916" not in (row.about_sanitized or "")
        assert "Иван" not in (row.about_sanitized or "") and "[fio]" in (row.about_sanitized or "")


# --- параметры SQL не утекают в ошибки и логи ---------------------------------------------------


async def test_db_errors_do_not_leak_parameters(
    db: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    def duplicate() -> AppUser:
        return AppUser(
            keycloak_sub="dup-sub", username="ivan.petrov@mail.ru", email="ivan.petrov@mail.ru"
        )

    async with db() as session:
        session.add(duplicate())
        await session.commit()
    async with db() as session:
        session.add(duplicate())
        with pytest.raises(IntegrityError) as exc:
            await session.flush()
        await session.rollback()
    with caplog.at_level(logging.ERROR, logger="app.errors"):
        logging.getLogger("app.errors").error("Необработанная ошибка", exc_info=exc.value)
    # hide_parameters: блока «[parameters: (...)]» с username/email нет ни в исключении,
    # ни в логе (DETAIL самого PostgreSQL может назвать значение уникального ключа —
    # keycloak_sub, псевдоним или HMAC, но не ПДн)
    for text_ in (str(exc.value), caplog.text):
        assert "[parameters:" not in text_ and "ivan.petrov" not in text_
        assert "SQL parameters hidden" in text_ or "parameters" not in text_
