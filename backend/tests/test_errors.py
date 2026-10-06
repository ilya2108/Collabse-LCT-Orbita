"""Единый формат ошибок {code, message, details, request_id} (PLAN.md §7.5)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.core.errors import (
    ApiError,
    RequestIdMiddleware,
    conflict,
    forbidden,
    incoming_request_id,
    not_found,
    not_implemented,
    rate_limited,
    register_error_handlers,
    stale_version,
    unauthorized,
    validation_error,
)


class _Body(BaseModel):
    email: str
    amount: int


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    @app.get("/boom-domain")
    async def boom_domain() -> None:
        raise ApiError(409, "role_already_set", "Роль уже выбрана")

    @app.post("/validated")
    async def validated(body: _Body) -> dict:
        return {"ok": True}

    @app.get("/boom-unexpected")
    async def boom_unexpected() -> None:
        raise RuntimeError("секретные детали: пароль=hunter2")

    @app.get("/boom-rate")
    async def boom_rate() -> None:
        raise rate_limited(retry_after_seconds=30)

    return TestClient(app, raise_server_exceptions=False)


def test_api_error_flat_body(client: TestClient) -> None:
    response = client.get("/boom-domain")
    assert response.status_code == 409
    body = response.json()
    assert set(body) == {"code", "message", "details", "request_id"}
    assert body["code"] == "role_already_set"
    assert body["message"] == "Роль уже выбрана"
    assert body["details"] == []
    assert body["request_id"]


def test_pydantic_validation_is_400_with_details(client: TestClient) -> None:
    # контракт: ошибки валидации — 400 validation_error, не 422 FastAPI
    response = client.post("/validated", json={"email": 1})
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["message"] == "Ошибка валидации данных"
    fields = {d["field"] for d in body["details"]}
    assert "amount" in fields


def test_unknown_route_is_wrapped_404(client: TestClient) -> None:
    response = client.get("/no-such-route")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert body["message"] == "Объект не найден"


def test_request_id_echoes_header(client: TestClient) -> None:
    rid = "11111111-2222-3333-4444-555555555555"
    response = client.get("/boom-domain", headers={"X-Request-Id": rid})
    assert response.headers["X-Request-Id"] == rid
    assert response.json()["request_id"] == rid


def test_unexpected_exception_hides_details(client: TestClient) -> None:
    response = client.get("/boom-unexpected", headers={"X-Request-Id": "abc-123"})
    assert response.status_code == 500
    body = response.json()
    assert set(body) == {"code", "message", "details", "request_id"}
    assert body["code"] == "internal_error"
    assert "hunter2" not in response.text
    # 500 — именно тот случай, когда id нужен для поиска в логах: тело и заголовок совпадают
    assert body["request_id"] == "abc-123"
    assert response.headers["X-Request-Id"] == "abc-123"


@pytest.mark.parametrize("raw", ["x" * 5000, "a b", "", "<script>", "id;drop"])
def test_malformed_incoming_request_id_is_replaced(client: TestClient, raw: str) -> None:
    response = client.get("/boom-domain", headers={"X-Request-Id": raw})
    rid = response.headers["X-Request-Id"]
    assert rid != raw and len(rid) == 36 and response.json()["request_id"] == rid


def test_incoming_request_id_format() -> None:
    assert incoming_request_id("req_01.ABC-9") == "req_01.ABC-9"
    assert incoming_request_id("a" * 64) == "a" * 64
    assert incoming_request_id("a" * 65) != "a" * 65
    assert incoming_request_id(None) and incoming_request_id("")


def test_rate_limited_sets_retry_after(client: TestClient) -> None:
    response = client.get("/boom-rate")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"
    assert response.json()["code"] == "rate_limited"


def test_error_factories() -> None:
    assert (unauthorized().status_code, unauthorized().code) == (401, "unauthorized")
    assert (forbidden().status_code, forbidden().code) == (403, "forbidden")
    assert (not_found().status_code, not_found().code) == (404, "not_found")
    assert (conflict().status_code, conflict().code) == (409, "conflict")
    assert conflict("Роль уже выбрана", code="role_already_set").code == "role_already_set"
    assert (stale_version().status_code, stale_version().code) == (409, "stale_version")
    assert (not_implemented().status_code, not_implemented().code) == (501, "not_implemented")
    err = validation_error(details=[{"field": "email", "code": "invalid_format", "message": "x"}])
    assert err.status_code == 400 and err.details is not None
