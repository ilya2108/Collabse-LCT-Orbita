"""Модуль tgbot: API привязки Telegram и внутренние ручки дайджеста (без PostgreSQL).

`POST /auth/telegram/link-token` — код показывается один раз, в БД — HMAC;
`POST /internal/digest/run` и `GET /internal/tgbot/outbox` — под X-Internal-Token.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.pii import get_pii_crypto
from app.models.tgbot import TgLink, TgLinkToken, TgOutboxMessage
from app.modules.tgbot.service import link_token_is_valid
from tests.conftest import INTERNAL_TOKEN, as_user, make_user
from tests.fake_session import FakeSession
from tests.test_employers_company import fake_db


class TestLinkToken:
    def test_anonymous_401(self, client: TestClient) -> None:
        assert client.post("/api/v1/auth/telegram/link-token").status_code == 401

    def test_requires_selectable_role(self, app: FastAPI, client: TestClient) -> None:
        with as_user(app, make_user(roles=[])), fake_db(app, FakeSession()):
            response = client.post("/api/v1/auth/telegram/link-token")
        assert response.status_code == 403

    def test_issues_one_time_code(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        session = FakeSession()
        with as_user(app, user), fake_db(app, session):
            response = client.post("/api/v1/auth/telegram/link-token")
        assert response.status_code == 201
        body = response.json()
        assert body["role"] == "candidate"
        assert body["code"] in body["deep_link"]
        assert body["deep_link"].startswith("https://t.me/")
        row = session.added_of(TgLinkToken)[0]
        # в БД — HMAC кода, не сам код; срок жизни в будущем
        assert row.token_hash == get_pii_crypto().hmac_hex(body["code"])
        assert row.expires_at > datetime.now(UTC)
        assert session.commits == 1

    def test_explicit_role_must_be_available(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        with as_user(app, user), fake_db(app, FakeSession()):
            response = client.post("/api/v1/auth/telegram/link-token", json={"role": "employer"})
        assert response.status_code == 403

    def test_status_anonymous_401(self, client: TestClient) -> None:
        assert client.get("/api/v1/auth/telegram/me").status_code == 401

    def test_status_not_linked(self, app: FastAPI, client: TestClient) -> None:
        session = FakeSession(scalars=[None])
        with as_user(app, make_user(roles=["candidate"])), fake_db(app, session):
            response = client.get("/api/v1/auth/telegram/me")
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "linked": False,
            "username_masked": None,
            "linked_at": None,
            "digest_enabled": False,
        }

    def test_status_linked(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        link = TgLink(
            tg_chat_id=100500, user_id=user.id, role="candidate", created_at=datetime.now(UTC)
        )
        with as_user(app, user), fake_db(app, FakeSession(scalars=[[link]])):
            response = client.get("/api/v1/auth/telegram/me")
        assert response.status_code == 200
        body = response.json()
        assert body["linked"] is True
        assert body["digest_enabled"] is True
        assert body["linked_at"] is not None
        # username в Telegram не хранится — ПДн-минимизация
        assert body["username_masked"] is None

    def test_token_validity_window(self) -> None:
        now = datetime.now(UTC)
        fresh = TgLinkToken(expires_at=now + timedelta(minutes=5), used_at=None)
        used = TgLinkToken(expires_at=now + timedelta(minutes=5), used_at=now)
        expired = TgLinkToken(expires_at=now - timedelta(minutes=1), used_at=None)
        assert link_token_is_valid(fresh, now)
        assert not link_token_is_valid(used, now)
        assert not link_token_is_valid(expired, now)
        assert not link_token_is_valid(None, now)


class TestInternalDigest:
    def test_requires_internal_token(self, client: TestClient) -> None:
        assert client.post("/api/v1/internal/digest/run").status_code == 401

    def test_run_now_without_links(self, app: FastAPI, client: TestClient) -> None:
        session = FakeSession(scalars=[[]])  # select(TgLink) → пусто
        with fake_db(app, session):
            response = client.post(
                "/api/v1/internal/digest/run", headers={"X-Internal-Token": INTERNAL_TOKEN}
            )
        assert response.status_code == 200
        assert response.json() == {"sent": 0, "mode": "stub"}

    def test_outbox_listing(self, app: FastAPI, client: TestClient) -> None:
        row = TgOutboxMessage(
            tg_chat_id=1001,
            kind="digest_manual",
            text="ФСП.Орбита — ваш дайджест",
            status="stub",
        )
        row.id = 1
        row.created_at = datetime.now(UTC)
        with fake_db(app, FakeSession(scalars=[[row]])):
            response = client.get(
                "/api/v1/internal/tgbot/outbox", headers={"X-Internal-Token": INTERNAL_TOKEN}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["kind"] == "digest_manual"
        assert "дайджест" in body["items"][0]["text"]
