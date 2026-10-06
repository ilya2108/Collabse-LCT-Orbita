"""Модуль tma: ручки мини-аппа через TestClient + FakeSession (без Postgres/Keycloak).

Авторизация в тестах — dev-режим `X-Telegram-Init-Data: dev:<tg_chat_id>`
(APP_ENV=test и TELEGRAM_BOT_MODE=stub выставлены в conftest); сама зависимость
`get_tma_actor` не подменяется и отрабатывает по-настоящему: находит `tg_link`,
достаёт пользователя или участника компании. Один тест проходит и настоящую
криптографию initData end-to-end.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.candidate import CandidateCategory
from app.models.employer import Company, CompanyMember
from app.models.tgbot import TgLink
from app.models.user import AppUser
from app.modules.tgbot.config import TgBotSettings
from tests.conftest import make_user
from tests.fake_session import FakeSession
from tests.test_employers_company import company_row, fake_db
from tests.test_invitations import invitation_row
from tests.test_matching_pipeline import entry_row
from tests.test_matching_rank import candidate_profile_row
from tests.test_needs_api import need_row
from tests.test_tma_initdata import BOT_TOKEN, sign, upstream_fields

CHAT_ID = 279058397  # совпадает с id пользователя в USER_JSON из test_tma_initdata
HEADERS = {"X-Telegram-Init-Data": f"dev:{CHAT_ID}"}


def link_row(
    *,
    user_id: uuid.UUID | None = None,
    member_id: uuid.UUID | None = None,
    role: str = "candidate",
) -> TgLink:
    link = TgLink(
        id=uuid.uuid4(), tg_chat_id=CHAT_ID, user_id=user_id, member_id=member_id, role=role
    )
    link.created_at = datetime.now(UTC)
    return link


def member_row(
    company_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    role: str = "hr",
    status: str = "invited",
) -> CompanyMember:
    now = datetime.now(UTC)
    member = CompanyMember(
        id=uuid.uuid4(), company_id=company_id, user_id=user_id, role=role, status=status
    )
    member.created_at = now
    member.updated_at = now
    return member


def candidate_setup() -> tuple[AppUser, TgLink, dict]:
    """Кандидат платформы, привязанный чат и rows для FakeSession."""
    user = make_user(roles=["candidate"])
    link = link_row(user_id=user.id, role="candidate")
    return user, link, {(AppUser, user.id): user}


def employer_setup() -> tuple[AppUser, Company, TgLink, dict]:
    user = make_user(roles=["employer"])
    company = company_row(user, name="Орбита")
    link = link_row(user_id=user.id, role="employer")
    return user, company, link, {(AppUser, user.id): user}


class TestTmaAuth:
    def test_no_header_401(self, app: FastAPI, client: TestClient) -> None:
        with fake_db(app, FakeSession()):
            response = client.get("/api/v1/tma/me")
        assert response.status_code == 401

    def test_not_linked_403(self, app: FastAPI, client: TestClient) -> None:
        with fake_db(app, FakeSession(scalars=[None])):
            response = client.get("/api/v1/tma/me", headers=HEADERS)
        assert response.status_code == 403
        body = response.json()
        assert body["code"] == "tma_not_linked"
        assert "привяжите бота" in body["message"]

    def test_dev_header_requires_stub_mode(
        self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """В polling-режиме dev-строка не принимается и уходит в настоящую валидацию."""
        monkeypatch.setattr(
            "app.modules.tma.deps.get_tgbot_settings",
            lambda: TgBotSettings(telegram_bot_mode="polling", telegram_bot_token=BOT_TOKEN),
        )
        with fake_db(app, FakeSession()):
            response = client.get("/api/v1/tma/me", headers=HEADERS)
        assert response.status_code == 401

    def test_dev_header_requires_dev_env(
        self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.modules.tma.deps.get_settings", lambda: SimpleNamespace(app_env="prod")
        )
        with fake_db(app, FakeSession()):
            response = client.get("/api/v1/tma/me", headers=HEADERS)
        assert response.status_code == 401

    def test_real_init_data_end_to_end(
        self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Настоящая подписанная initData проходит весь путь до ответа /tma/me."""
        monkeypatch.setattr(
            "app.modules.tma.deps.get_tgbot_settings",
            lambda: TgBotSettings(telegram_bot_mode="polling", telegram_bot_token=BOT_TOKEN),
        )
        user, link, rows = candidate_setup()
        profile = candidate_profile_row(user_id=user.id)
        raw = sign(upstream_fields(auth_date=datetime.now(UTC)))
        session = FakeSession(scalars=[link, profile, None], rows=rows)
        with fake_db(app, session):
            response = client.get("/api/v1/tma/me", headers={"X-Telegram-Init-Data": raw})
        assert response.status_code == 200
        assert response.json()["pseudonym"] == profile.pseudonym

    def test_revoked_member_403(self, app: FastAPI, client: TestClient) -> None:
        member = member_row(uuid.uuid4(), status="revoked")
        link = link_row(member_id=member.id, role="employer")
        session = FakeSession(scalars=[link], rows={(CompanyMember, member.id): member})
        with fake_db(app, session):
            response = client.get("/api/v1/tma/me", headers=HEADERS)
        assert response.status_code == 403
        assert response.json()["code"] == "tma_not_linked"


class TestTmaMe:
    def test_candidate_with_grade(self, app: FastAPI, client: TestClient) -> None:
        user, link, rows = candidate_setup()
        profile = candidate_profile_row(user_id=user.id)
        category = CandidateCategory(
            id=uuid.uuid4(),
            candidate_id=profile.id,
            specialization_code="be",
            grade="middle",
            q_level=2,
            shape="T",
        )
        session = FakeSession(scalars=[link, profile, category], rows=rows)
        with fake_db(app, session):
            response = client.get("/api/v1/tma/me", headers=HEADERS)
        assert response.status_code == 200
        assert response.json() == {
            "kind": "candidate",
            "pseudonym": "B-7F3A",
            "grade": "middle",
            "company_name": None,
            "role": None,
        }

    def test_candidate_without_profile(self, app: FastAPI, client: TestClient) -> None:
        _, link, rows = candidate_setup()
        session = FakeSession(scalars=[link, None], rows=rows)
        with fake_db(app, session):
            response = client.get("/api/v1/tma/me", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["pseudonym"] is None

    def test_employer(self, app: FastAPI, client: TestClient) -> None:
        _, company, link, rows = employer_setup()
        session = FakeSession(scalars=[link, company], rows=rows)
        with fake_db(app, session):
            response = client.get("/api/v1/tma/me", headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "employer"
        assert body["company_name"] == "Орбита"
        assert body["role"] == "owner"

    def test_member_without_account(self, app: FastAPI, client: TestClient) -> None:
        company = company_row(make_user(roles=["employer"]), name="Орбита")
        member = member_row(company.id, role="hr")
        link = link_row(member_id=member.id, role="employer")
        session = FakeSession(
            scalars=[link],
            rows={(CompanyMember, member.id): member, (Company, company.id): company},
        )
        with fake_db(app, session):
            response = client.get("/api/v1/tma/me", headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "kind": "member",
            "pseudonym": None,
            "grade": None,
            "company_name": "Орбита",
            "role": "hr",
        }


class TestTmaInvitations:
    def test_list_as_in_web(self, app: FastAPI, client: TestClient) -> None:
        user, link, rows = candidate_setup()
        profile = candidate_profile_row(user_id=user.id)
        company = company_row(make_user(roles=["employer"]), name="Орбита")
        sent = invitation_row(company.id, profile.id, contact_method="tg @orbita")
        accepted = invitation_row(
            company.id, profile.id, status="accepted", contact_method="tg @orbita"
        )
        session = FakeSession(scalars=[link, profile, [sent, accepted], [company]], rows=rows)
        with fake_db(app, session):
            response = client.get("/api/v1/tma/invitations", headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        by_status = {item["status"]: item for item in body["items"]}
        assert by_status["sent"]["company_name"] == "Орбита"
        # способ связи виден только после принятия — как в вебе
        assert by_status["sent"]["contact_method"] is None
        assert by_status["accepted"]["contact_method"] == "tg @orbita"

    def test_list_forbidden_for_employer(self, app: FastAPI, client: TestClient) -> None:
        _, _, link, rows = employer_setup()
        session = FakeSession(scalars=[link], rows=rows)
        with fake_db(app, session):
            response = client.get("/api/v1/tma/invitations", headers=HEADERS)
        assert response.status_code == 403

    def test_accept_returns_company_contacts(self, app: FastAPI, client: TestClient) -> None:
        user, link, rows = candidate_setup()
        profile = candidate_profile_row(user_id=user.id)
        company = company_row(make_user(roles=["employer"]), name="Орбита")
        company.contact_email = "hiring@orbita.local"
        invitation = invitation_row(company.id, profile.id, contact_method="tg @orbita")
        session = FakeSession(
            scalars=[link, profile, invitation],
            rows={**rows, (Company, company.id): company},
        )
        with fake_db(app, session):
            response = client.post(
                f"/api/v1/tma/invitations/{invitation.id}/accept", headers=HEADERS
            )
        assert response.status_code == 200
        body = response.json()
        assert body["invitation"]["status"] == "accepted"
        assert body["company_contacts"]["company_name"] == "Орбита"
        assert body["company_contacts"]["contact_email"] == "hiring@orbita.local"
        assert session.commits == 1

    def test_accept_closed_409(self, app: FastAPI, client: TestClient) -> None:
        user, link, rows = candidate_setup()
        profile = candidate_profile_row(user_id=user.id)
        invitation = invitation_row(uuid.uuid4(), profile.id, status="declined")
        session = FakeSession(scalars=[link, profile, invitation], rows=rows)
        with fake_db(app, session):
            response = client.post(
                f"/api/v1/tma/invitations/{invitation.id}/accept", headers=HEADERS
            )
        assert response.status_code == 409
        assert response.json()["code"] == "invitation_closed"

    def test_decline_with_reason(self, app: FastAPI, client: TestClient) -> None:
        user, link, rows = candidate_setup()
        profile = candidate_profile_row(user_id=user.id)
        invitation = invitation_row(uuid.uuid4(), profile.id)
        session = FakeSession(scalars=[link, profile, invitation], rows=rows)
        with fake_db(app, session):
            response = client.post(
                f"/api/v1/tma/invitations/{invitation.id}/decline",
                headers=HEADERS,
                json={"reason_code": "busy"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "declined"
        assert body["decline_reason_code"] == "busy"

    def test_decline_without_body(self, app: FastAPI, client: TestClient) -> None:
        user, link, rows = candidate_setup()
        profile = candidate_profile_row(user_id=user.id)
        invitation = invitation_row(uuid.uuid4(), profile.id)
        session = FakeSession(scalars=[link, profile, invitation], rows=rows)
        with fake_db(app, session):
            response = client.post(
                f"/api/v1/tma/invitations/{invitation.id}/decline", headers=HEADERS
            )
        assert response.status_code == 200
        assert response.json()["decline_reason_code"] is None


class TestTmaNeeds:
    def test_employer_sees_only_active(self, app: FastAPI, client: TestClient) -> None:
        _, company, link, rows = employer_setup()
        active = need_row(company.id)
        archived = need_row(company.id, status="archived", title="Старая")
        session = FakeSession(scalars=[link, company, [active, archived]], rows=rows)
        with fake_db(app, session):
            response = client.get("/api/v1/tma/needs", headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == str(active.id)

    def test_member_sees_own_company_needs(self, app: FastAPI, client: TestClient) -> None:
        company = company_row(make_user(roles=["employer"]), name="Орбита")
        member = member_row(company.id)
        link = link_row(member_id=member.id, role="employer")
        need = need_row(company.id)
        session = FakeSession(
            scalars=[link, [need]],
            rows={(CompanyMember, member.id): member, (Company, company.id): company},
        )
        with fake_db(app, session):
            response = client.get("/api/v1/tma/needs", headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["items"][0]["company_id"] == str(company.id)

    def test_forbidden_for_candidate(self, app: FastAPI, client: TestClient) -> None:
        _, link, rows = candidate_setup()
        session = FakeSession(scalars=[link], rows=rows)
        with fake_db(app, session):
            response = client.get("/api/v1/tma/needs", headers=HEADERS)
        assert response.status_code == 403


class TestTmaPipeline:
    def test_columns_as_in_web(self, app: FastAPI, client: TestClient) -> None:
        user, company, link, rows = employer_setup()
        need = need_row(company.id)
        profile = candidate_profile_row()
        entry = entry_row(need.id, profile.id)
        session = FakeSession(
            scalars=[link, company, need, [entry], None, [profile], [(profile.id, "high")]],
            rows=rows,
        )
        with fake_db(app, session):
            response = client.get(f"/api/v1/tma/needs/{need.id}/pipeline", headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        columns = {column["stage"]: column["items"] for column in body["columns"]}
        item = columns["shortlist"][0]
        assert item["pseudonym"] == "B-7F3A"
        assert item["integrity_level"] == "high"
        assert item["contacts_available"] is False

    def test_foreign_need_404(self, app: FastAPI, client: TestClient) -> None:
        _, company, link, rows = employer_setup()
        session = FakeSession(scalars=[link, company, None], rows=rows)
        with fake_db(app, session):
            response = client.get(f"/api/v1/tma/needs/{uuid.uuid4()}/pipeline", headers=HEADERS)
        assert response.status_code == 404

    def test_stage_change_after_accept(self, app: FastAPI, client: TestClient) -> None:
        _, company, link, rows = employer_setup()
        need = need_row(company.id)
        entry = entry_row(need.id, uuid.uuid4(), stage="accepted")
        session = FakeSession(scalars=[link, company, need, entry], rows=rows)
        with fake_db(app, session):
            response = client.patch(
                f"/api/v1/tma/needs/{need.id}/pipeline/{entry.candidate_id}",
                headers=HEADERS,
                json={"stage": "interview"},
            )
        assert response.status_code == 200
        assert response.json()["stage"] == "interview"
        assert session.commits == 1

    def test_interview_requires_accept_409(self, app: FastAPI, client: TestClient) -> None:
        _, company, link, rows = employer_setup()
        need = need_row(company.id)
        entry = entry_row(need.id, uuid.uuid4(), stage="shortlist")
        session = FakeSession(scalars=[link, company, need, entry], rows=rows)
        with fake_db(app, session):
            response = client.patch(
                f"/api/v1/tma/needs/{need.id}/pipeline/{entry.candidate_id}",
                headers=HEADERS,
                json={"stage": "interview"},
            )
        assert response.status_code == 409
        assert response.json()["code"] == "pipeline_transition_invalid"

    def test_member_without_account_acts_as_owner(self, app: FastAPI, client: TestClient) -> None:
        """«HR через Telegram»: действия выполняются от имени владельца компании."""
        owner = make_user(roles=["employer"])
        company = company_row(owner, name="Орбита")
        member = member_row(company.id)
        link = link_row(member_id=member.id, role="employer")
        need = need_row(company.id)
        entry = entry_row(need.id, uuid.uuid4(), stage="accepted")
        session = FakeSession(
            scalars=[link, company, need, entry],
            rows={
                (CompanyMember, member.id): member,
                (Company, company.id): company,
                (AppUser, owner.id): owner,
            },
        )
        with fake_db(app, session):
            response = client.patch(
                f"/api/v1/tma/needs/{need.id}/pipeline/{entry.candidate_id}",
                headers=HEADERS,
                json={"stage": "interview"},
            )
        assert response.status_code == 200
        assert response.json()["stage"] == "interview"
