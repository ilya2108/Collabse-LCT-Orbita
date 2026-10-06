"""Смоук каркаса: маппинг моделей, реестр роутеров, health, internal-токен, 401-конверт."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import configure_mappers

import app.models  # noqa: F401 — регистрирует все модели
from app.core.config import Settings, get_settings
from app.core.security import (
    KNOWN_ROLES,
    check_roles,
    default_active_role,
    merge_roles,
    parse_token_payload,
    validate_audience,
    verify_internal_token,
)
from app.models.base import Base
from tests.conftest import INTERNAL_TOKEN, as_user, make_user

EXPECTED_TABLES = {"app_user", "llm_call", "audit_log"}
EXPECTED_PREFIXES = (
    "/api/v1/auth",
    "/api/v1/skills",
    "/api/v1/candidates",
    "/api/v1/assessment",
    "/api/v1/llm",
    "/api/v1/matching",
    "/api/v1/employers",
    "/api/v1/fsp",
    "/api/v1/internal",
)


def test_models_configure_and_tables_present() -> None:
    configure_mappers()
    assert EXPECTED_TABLES <= set(Base.metadata.tables)
    app_user = Base.metadata.tables["app_user"]
    assert "email_enc" in app_user.c and "email" not in app_user.c  # ПДн только шифртекстом


def test_settings_from_test_env() -> None:
    settings = get_settings()
    assert settings.app_env == "test" and settings.is_test
    assert settings.llm_provider == "stub"
    assert settings.keycloak_realm == "fsp"
    assert settings.jwks_url.endswith("/realms/fsp/protocol/openid-connect/certs")
    assert settings.skills_path.name == "skills" and settings.skills_path.is_absolute()


def test_settings_fail_closed() -> None:
    """Опечатка в APP_ENV, пустой audience в prod и отсутствующие/битые ключи ПДн роняют
    старт, а не первый аутентифицированный запрос."""
    secret = "a" * 64
    with pytest.raises(ValidationError):
        Settings(app_env="production")
    with pytest.raises(ValidationError):  # dev-дефолт INTERNAL_API_TOKEN в prod
        Settings(app_env="prod", internal_api_token="dev-internal-token")
    with pytest.raises(ValidationError):
        Settings(app_env="prod", internal_api_token=secret, keycloak_audiences=" , ")
    assert Settings(app_env="prod", internal_api_token=secret).app_env == "prod"
    with pytest.raises(ValidationError):
        Settings(pii_fernet_keys="")
    with pytest.raises(ValidationError):
        Settings(pii_fernet_keys="not-a-fernet-key")
    with pytest.raises(ValidationError):
        Settings(pii_hmac_key="короткий")
    assert not validate_audience({"aud": "account"}, [])  # пустой список — fail-closed
    assert validate_audience({"aud": ["account"], "azp": "fsp-frontend"}, ["fsp-frontend"])


def test_health_live(client: TestClient) -> None:
    for path in ("/health/live", "/api/v1/health/live", "/healthz"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.json() == {"status": "ok"}


def test_openapi_lists_registered_routes(client: TestClient) -> None:
    schema = client.get("/api/v1/openapi.json").json()
    assert schema["info"]["title"].startswith("«Орбита ФСП»")
    # роутеры-заглушки без эндпоинтов путей не дают; ping — единственный эндпоинт каркаса
    assert "/api/v1/internal/ping" in schema["paths"]
    assert all(path.startswith("/api/v1/") for path in schema["paths"])
    assert all(prefix.startswith("/api/v1/") for prefix in EXPECTED_PREFIXES)


def test_internal_ping_requires_token(client: TestClient) -> None:
    response = client.post("/api/v1/internal/ping")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"

    response = client.post("/api/v1/internal/ping", headers={"X-Internal-Token": "wrong"})
    assert response.status_code == 401

    response = client.post("/api/v1/internal/ping", headers={"X-Internal-Token": INTERNAL_TOKEN})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["llm_provider"] == "stub" and body["request_id"]


def test_internal_token_constant_time_compare() -> None:
    assert verify_internal_token("abc", "abc")
    assert not verify_internal_token("abc", "abd")
    assert not verify_internal_token(None, "abc")
    assert not verify_internal_token("abc", "")


def test_parse_token_filters_roles_and_reads_email_verified() -> None:
    claims = parse_token_payload(
        {
            "sub": "11111111-1111-1111-1111-111111111111",
            "preferred_username": "anna",
            "email": "Anna@Example.com",
            "email_verified": True,
            "realm_access": {"roles": ["offline_access", "candidate", "uma_authorization"]},
            "azp": "fsp-frontend",
        }
    )
    assert claims.roles == ("candidate",)
    assert claims.email_verified is True
    assert claims.has_any_role(KNOWN_ROLES)


def test_merge_roles_union_and_default_active_role() -> None:
    assert merge_roles(["candidate"], ("admin",)) == ["admin", "candidate"]
    assert merge_roles([], ()) == []
    assert merge_roles(["employer"], ("unknown_role",)) == ["employer"]
    assert default_active_role(["admin"]) is None
    assert default_active_role(["employer", "admin"]) == "employer"
    assert check_roles(["candidate"], ("candidate", "admin"))
    assert not check_roles([], ("candidate",))


def test_as_user_override_works_for_protected_route() -> None:
    """Подмена get_current_user — основной способ тестировать роутеры без Keycloak и БД."""
    from fastapi import APIRouter, Depends

    from app.core.errors import RequestIdMiddleware, register_error_handlers
    from app.core.security import get_current_user, require_roles

    probe = APIRouter(prefix="/probe")

    @probe.get("/me")
    async def me(user=Depends(get_current_user)) -> dict:
        return {"roles": user.roles, "active_role": user.active_role}

    @probe.get("/employer-only", dependencies=[Depends(require_roles("employer"))])
    async def employer_only() -> dict:
        return {"ok": True}

    probe_app = FastAPI()
    probe_app.add_middleware(RequestIdMiddleware)
    register_error_handlers(probe_app)
    probe_app.include_router(probe)
    client = TestClient(probe_app, raise_server_exceptions=False)

    # без токена — 401 в едином формате
    response = client.get("/probe/me")
    assert response.status_code == 401 and response.json()["code"] == "unauthorized"
    with as_user(probe_app, make_user(roles=[])):
        assert client.get("/probe/me").json() == {"roles": [], "active_role": None}
        assert client.get("/probe/employer-only").status_code == 403
    with as_user(probe_app, make_user(roles=["employer"])):
        assert client.get("/probe/employer-only").json() == {"ok": True}
    # после выхода из контекста подмена снята
    assert client.get("/probe/me").status_code == 401
