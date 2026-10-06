"""Тестовое окружение: env-переменные задаются ДО импорта app.core.config.

Тесты каркаса не требуют PostgreSQL и Keycloak: роутеры проверяются через TestClient
с подменой зависимостей (`as_user`), чистые функции — напрямую. Интеграционные тесты
с БД (если появятся) помечаются `@pytest.mark.db` и запускаются отдельно.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from cryptography.fernet import Fernet

os.environ["APP_ENV"] = "test"
os.environ["PII_FERNET_KEYS"] = Fernet.generate_key().decode()
os.environ["PII_HMAC_KEY"] = "test-hmac-key-0123456789abcdef-0123456789abcdef"
os.environ["KEYCLOAK_INTERNAL_URL"] = "http://keycloak.test:8080"
os.environ["KEYCLOAK_ISSUER"] = "http://localhost:8080/realms/fsp"
os.environ["KEYCLOAK_REALM"] = "fsp"
os.environ["KEYCLOAK_AUDIENCES"] = "account,fsp-frontend"
os.environ["INTERNAL_API_TOKEN"] = "test-internal-token"
os.environ["DATABASE_URL"] = "postgresql+asyncpg://fsp:fsp@localhost:5432/fsp_test"
os.environ["LLM_PROVIDER"] = "stub"
os.environ["TEST_DEMO_MODE"] = "true"
os.environ["DEMO_COOLDOWN_SECONDS"] = "1"

import pytest  # noqa: E402 — после настройки env
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.security import get_current_user  # noqa: E402
from app.models.user import AppUser  # noqa: E402

INTERNAL_TOKEN = os.environ["INTERNAL_API_TOKEN"]


def make_user(
    *,
    roles: list[str] | None = None,
    active_role: str | None = None,
    email: str | None = "user@example.com",
    consent: bool = True,
) -> AppUser:
    """Пользователь в памяти (без БД) для подмены get_current_user в тестах роутеров.

    `roles` — эффективные роли: candidate/employer кладутся в `roles` (как в БД),
    `admin` — в `token_roles` (как из токена Keycloak; в БД он не хранится).
    """
    roles = list(roles or [])
    now = datetime.now(UTC)
    user = AppUser(
        id=uuid.uuid4(),
        keycloak_sub=str(uuid.uuid4()),
        username="test-user",
        email=email,
        email_verified=True,
        roles=[r for r in roles if r != "admin"],
        token_roles=tuple(r for r in roles if r == "admin"),
        active_role=active_role or (roles[0] if roles else None),
        consent_pdn_at=now if consent else None,
        consent_policy_version="v1" if consent else None,
    )
    user.created_at = now
    user.updated_at = now
    return user


@contextmanager
def as_user(app: FastAPI, user: AppUser) -> Iterator[None]:
    """Контекст: все запросы к `app` выполняются от имени `user` (JWT не нужен)."""
    previous = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = previous


@pytest.fixture()
def app() -> FastAPI:
    from app.main import app as fastapi_app

    return fastapi_app


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
