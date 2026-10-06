"""Модуль employers: участники компании — приглашение эйчара/менеджера БЕЗ входа
на платформу (одноразовый токен, HMAC в БД), отзыв, принятие, маскирование ПДн."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.pii import get_pii_crypto
from app.models.audit import AuditLog
from app.models.employer import Company, CompanyMember
from app.modules.employers.service import mask_telegram
from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession
from tests.test_employers_company import company_row, fake_db


def member_row(company: Company, **overrides) -> CompanyMember:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "company_id": company.id,
        "user_id": None,
        "role": "hr",
        "status": "invited",
        "created_at": now,
        "updated_at": now,
    }
    return CompanyMember(**{**base, **overrides})


class TestInvite:
    def test_personal_company_409(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        session = FakeSession(scalars=[company_row(user, kind="personal")])
        with as_user(app, user), fake_db(app, session):
            response = client.post(
                "/api/v1/employers/me/company/members/invite",
                json={"role": "hr", "channel": "email", "email": "hr@fsp.local"},
            )
        assert response.status_code == 409
        assert response.json()["code"] == "company_kind_personal"

    def test_invite_returns_one_time_token(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user, kind="corporate")
        # компания → проверка дубликата по контакту
        session = FakeSession(scalars=[company, None])
        with as_user(app, user), fake_db(app, session):
            response = client.post(
                "/api/v1/employers/me/company/members/invite",
                json={
                    "role": "hr",
                    "channel": "email",
                    "email": "Anna.HR@fsp.local",
                    "full_name": "Орбитина Анна",
                },
            )
        assert response.status_code == 201
        body = response.json()
        token = body["invite_token"]
        assert token and token in body["invite_url"]
        # ПДн в ответе — только маскированные
        assert body["member"]["email_masked"].endswith("@fsp.local")
        assert "anna.hr" not in body["member"]["email_masked"]
        assert body["member"]["status"] == "invited"
        assert body["member"]["has_account"] is False
        member = session.added_of(CompanyMember)[0]
        # в БД — HMAC токена, не сам токен; email нормализован; blind index заполнен
        assert member.invite_token_hash == get_pii_crypto().hmac_hex(token)
        assert member.email == "anna.hr@fsp.local"
        assert len(member.email_hash) == 64
        assert member.user_id is None
        assert session.added_of(AuditLog), "приглашение должно попадать в аудит"

    def test_telegram_channel_requires_telegram(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        with as_user(app, user), fake_db(app, FakeSession()):
            response = client.post(
                "/api/v1/employers/me/company/members/invite",
                json={"role": "hr", "channel": "telegram"},
            )
        assert response.status_code == 400

    def test_duplicate_contact_409(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user, kind="corporate")
        session = FakeSession(scalars=[company, member_row(company)])
        with as_user(app, user), fake_db(app, session):
            response = client.post(
                "/api/v1/employers/me/company/members/invite",
                json={"role": "hr", "channel": "email", "email": "hr@fsp.local"},
            )
        assert response.status_code == 409
        assert response.json()["code"] == "member_already_invited"

    def test_hiring_manager_cannot_invite(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        owner = make_user(roles=["employer"])
        company = company_row(owner, kind="corporate")
        membership = member_row(company, user_id=user.id, role="hiring_manager", status="active")
        session = FakeSession(scalars=[None, membership], rows={(Company, company.id): company})
        with as_user(app, user), fake_db(app, session):
            response = client.post(
                "/api/v1/employers/me/company/members/invite",
                json={"role": "hr", "channel": "email", "email": "hr@fsp.local"},
            )
        assert response.status_code == 403


class TestRevoke:
    def test_owner_not_revocable(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user, kind="corporate")
        owner_member = member_row(company, role="owner", status="active", user_id=user.id)
        session = FakeSession(scalars=[company, owner_member])
        with as_user(app, user), fake_db(app, session):
            response = client.delete(f"/api/v1/employers/me/company/members/{owner_member.id}")
        assert response.status_code == 409
        assert response.json()["code"] == "owner_not_revocable"

    def test_revoke_invited_member(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user, kind="corporate")
        member = member_row(company, invite_token_hash="a" * 64)
        session = FakeSession(scalars=[company, member])
        with as_user(app, user), fake_db(app, session):
            response = client.delete(f"/api/v1/employers/me/company/members/{member.id}")
        assert response.status_code == 204
        assert member.status == "revoked"
        assert member.invite_token_hash is None
        assert member.revoked_at is not None


class TestAcceptInvite:
    def test_accept_binds_user_and_adds_role(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=[])  # приглашённый без роли на платформе
        company = company_row(make_user(roles=["employer"]), kind="corporate")
        member = member_row(company, invite_token_hash=get_pii_crypto().hmac_hex("tok-123456"))
        session = FakeSession(scalars=[member], rows={(Company, company.id): company})
        with as_user(app, user), fake_db(app, session):
            response = client.post(
                "/api/v1/employers/company/invites/accept", json={"token": "tok-123456"}
            )
        assert response.status_code == 200
        assert response.json()["my_role"] == "hr"
        assert member.user_id == user.id
        assert member.status == "active"
        assert member.invite_token_hash is None
        assert "employer" in user.roles

    def test_used_token_404(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=[])
        session = FakeSession(scalars=[None])
        with as_user(app, user), fake_db(app, session):
            response = client.post(
                "/api/v1/employers/company/invites/accept", json={"token": "tok-missing"}
            )
        assert response.status_code == 404


class TestInviteInfoPublic:
    def test_public_info_by_token(self, app: FastAPI, client: TestClient) -> None:
        company = company_row(make_user(roles=["employer"]), kind="corporate", name="Орбита")
        member = member_row(company)
        session = FakeSession(scalars=[member], rows={(Company, company.id): company})
        with fake_db(app, session):
            response = client.get("/api/v1/employers/company/invites/any-token")
        assert response.status_code == 200
        body = response.json()
        assert body == {"company_name": "Орбита", "role": "hr", "status": "invited"}


class TestMembersList:
    def test_masks_pii(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user, kind="corporate")
        member = member_row(
            company, full_name="Орбитина Анна", email="anna@fsp.local", telegram="anna_hr"
        )
        session = FakeSession(scalars=[company, [member]])
        with as_user(app, user), fake_db(app, session):
            response = client.get("/api/v1/employers/me/company/members")
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["full_name_masked"] == "О***а А."
        assert item["email_masked"] == "a***a@fsp.local"
        assert item["telegram_masked"] == mask_telegram("anna_hr")
        assert "anna@fsp.local" not in str(response.json())


def test_mask_telegram() -> None:
    assert mask_telegram("anna_hr") == "a***r"
    assert mask_telegram("ab") == "a***"
