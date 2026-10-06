"""Модуль matching: приглашения — лимиты §5.6 (юниты + роутер), снимок с зарплатой,
предупреждение «ниже планки» без раскрытия суммы, accept/decline кандидата,
contact_grant и раскрытие контактов только после принятия."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.audit import AuditLog
from app.models.employer import (
    ContactGrant,
    Invitation,
    PipelineStageEvent,
)
from app.modules.matching import service
from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession
from tests.test_employers_company import company_row, fake_db
from tests.test_matching_pipeline import entry_row
from tests.test_matching_rank import candidate_profile_row
from tests.test_needs_api import need_row


def invitation_row(company_id: uuid.UUID, candidate_id: uuid.UUID, **overrides) -> Invitation:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "company_id": company_id,
        "candidate_id": candidate_id,
        "need_id": None,
        "title": "Приглашение",
        "offer_text": "Текст",
        "salary_from": 190_000,
        "salary_to": 240_000,
        "explanation": {"text": "Приглашение из банка"},
        "status": "sent",
        "sent_at": now,
        "expires_at": now + timedelta(days=14),
        "created_at": now,
    }
    return Invitation(**{**base, **overrides})


class TestQuotaRules:
    def test_open_pair_blocks(self) -> None:
        error = service.quota_error(1, 0, True, 0, 0)
        assert error is not None and error.code == "invitation_already_open"

    def test_scope_limit(self) -> None:
        error = service.quota_error(0, service.MAX_OPEN_PER_NEED, True, 0, 0)
        assert error is not None and error.code == "invitation_quota"

    def test_company_limit(self) -> None:
        error = service.quota_error(0, 0, False, service.MAX_OPEN_PER_COMPANY, 0)
        assert error is not None and error.code == "invitation_quota"

    def test_candidate_daily_limit(self) -> None:
        error = service.quota_error(0, 0, True, 0, service.MAX_CANDIDATE_PER_DAY)
        assert error is not None and error.code == "candidate_daily_limit"

    def test_ok(self) -> None:
        assert service.quota_error(0, 9, True, 29, 4) is None


class TestCreateInvitation:
    def test_salary_required_and_valid(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        with as_user(app, user), fake_db(app, FakeSession()):
            response = client.post(
                "/api/v1/invitations",
                json={"candidate_id": str(uuid.uuid4()), "salary_from": 10, "salary_to": 5},
            )
        assert response.status_code == 400

    def test_create_without_need_below_floor_warning(
        self, app: FastAPI, client: TestClient
    ) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user, name="Орбита")
        candidate = candidate_profile_row(salary_floor=300_000)
        # компания → кандидат → счётчики: пара, без потребности, по компании, суточный
        session = FakeSession(scalars=[company, candidate, 0, 0, 0, 0])
        with as_user(app, user), fake_db(app, session):
            response = client.post(
                "/api/v1/invitations",
                json={
                    "candidate_id": str(candidate.id),
                    "salary_from": 150_000,
                    "salary_to": 200_000,
                    "message": "Приходите к нам",
                },
            )
        assert response.status_code == 201
        body = response.json()
        # «ниже планки» — без раскрытия суммы планки
        assert body["below_salary_floor"] is True
        assert "300" not in str(body)
        assert body["invitation"]["status"] == "sent"
        invitation = session.added_of(Invitation)[0]
        assert invitation.need_id is None
        assert invitation.expires_at - invitation.sent_at == timedelta(
            days=service.INVITATION_TTL_DAYS
        )
        assert session.added_of(AuditLog)

    def test_create_with_need_moves_pipeline(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)
        candidate = candidate_profile_row()
        entry = entry_row(need.id, candidate.id)  # уже в шорт-листе
        # компания → потребность → кандидат → 4 счётчика → строка воронки
        session = FakeSession(scalars=[company, need, candidate, 0, 0, 0, 0, entry])
        with as_user(app, user), fake_db(app, session):
            response = client.post(
                "/api/v1/invitations",
                json={
                    "candidate_id": str(candidate.id),
                    "need_id": str(need.id),
                    "salary_from": 190_000,
                    "salary_to": 240_000,
                },
            )
        assert response.status_code == 201
        assert entry.stage == "invited"
        assert entry.invitation_id is not None
        events = session.added_of(PipelineStageEvent)
        assert [(e.from_stage, e.to_stage) for e in events] == [("shortlist", "invited")]
        explanation = response.json()["invitation"]["explanation"]
        assert explanation["need_id"] == str(need.id)

    def test_open_pair_409(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        candidate = candidate_profile_row()
        session = FakeSession(scalars=[company, candidate, 1, 0, 0, 0])
        with as_user(app, user), fake_db(app, session):
            response = client.post(
                "/api/v1/invitations",
                json={
                    "candidate_id": str(candidate.id),
                    "salary_from": 100_000,
                    "salary_to": 150_000,
                },
            )
        assert response.status_code == 409
        assert response.json()["code"] == "invitation_already_open"


class TestCandidateSide:
    def test_accept_creates_grant_and_reveals_company(
        self, app: FastAPI, client: TestClient
    ) -> None:
        user = make_user(roles=["candidate"])
        profile = candidate_profile_row(user_id=user.id)
        employer_user_id = uuid.uuid4()
        company = company_row(make_user(roles=["employer"]), name="Орбита")
        company.user_id = employer_user_id
        company.contact_email = "hiring@orbita.local"
        invitation = invitation_row(company.id, profile.id, contact_method="tg @orbita")
        from app.models.employer import Company

        session = FakeSession(scalars=[profile, invitation], rows={(Company, company.id): company})
        with as_user(app, user), fake_db(app, session):
            response = client.post(f"/api/v1/invitations/{invitation.id}/accept")
        assert response.status_code == 200
        body = response.json()
        assert body["invitation"]["status"] == "accepted"
        assert body["company_contacts"]["company_name"] == "Орбита"
        assert body["company_contacts"]["contact_email"] == "hiring@orbita.local"
        grant = session.added_of(ContactGrant)[0]
        assert grant.basis == "invitation"
        assert grant.ref_id == invitation.id
        assert grant.employer_user_id == employer_user_id
        assert invitation.decided_at is not None

    def test_accept_foreign_404(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = candidate_profile_row(user_id=user.id)
        foreign = invitation_row(uuid.uuid4(), uuid.uuid4())
        session = FakeSession(scalars=[profile, foreign])
        with as_user(app, user), fake_db(app, session):
            response = client.post(f"/api/v1/invitations/{foreign.id}/accept")
        assert response.status_code == 404

    def test_accept_closed_409(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = candidate_profile_row(user_id=user.id)
        invitation = invitation_row(uuid.uuid4(), profile.id, status="declined")
        session = FakeSession(scalars=[profile, invitation])
        with as_user(app, user), fake_db(app, session):
            response = client.post(f"/api/v1/invitations/{invitation.id}/accept")
        assert response.status_code == 409
        assert response.json()["code"] == "invitation_closed"

    def test_accept_expired_409(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = candidate_profile_row(user_id=user.id)
        invitation = invitation_row(
            uuid.uuid4(), profile.id, expires_at=datetime.now(UTC) - timedelta(days=1)
        )
        session = FakeSession(scalars=[profile, invitation])
        with as_user(app, user), fake_db(app, session):
            response = client.post(f"/api/v1/invitations/{invitation.id}/accept")
        assert response.status_code == 409
        assert response.json()["code"] == "invitation_expired"

    def test_decline_with_reason_moves_pipeline(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = candidate_profile_row(user_id=user.id)
        need_id = uuid.uuid4()
        invitation = invitation_row(uuid.uuid4(), profile.id, need_id=need_id)
        entry = entry_row(need_id, profile.id, stage="invited")
        session = FakeSession(scalars=[profile, invitation, entry])
        with as_user(app, user), fake_db(app, session):
            response = client.post(
                f"/api/v1/invitations/{invitation.id}/decline",
                json={"reason_code": "salary", "text": "ожидания выше"},
            )
        assert response.status_code == 200
        assert invitation.status == "declined"
        assert invitation.decline_reason_code == "salary"
        assert entry.stage == "declined"

    def test_incoming_list_with_company_names(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = candidate_profile_row(user_id=user.id)
        company = company_row(make_user(roles=["employer"]), name="Орбита")
        invitation = invitation_row(company.id, profile.id)
        session = FakeSession(scalars=[profile, [invitation], [company]])
        with as_user(app, user), fake_db(app, session):
            response = client.get("/api/v1/candidates/me/invitations")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["company_name"] == "Орбита"
        # способ связи скрыт до принятия
        assert body["items"][0]["contact_method"] is None


class TestContacts:
    def test_contacts_require_grant(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        invitation = invitation_row(company.id, uuid.uuid4())
        # компания → приглашение → grant (нет)
        session = FakeSession(scalars=[company, invitation, None])
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/invitations/{invitation.id}/contacts")
        assert response.status_code == 403

    def test_contacts_revealed_with_grant_and_audited(
        self, app: FastAPI, client: TestClient
    ) -> None:
        from app.models.employer import ContactRevealAudit

        user = make_user(roles=["employer"])
        company = company_row(user)
        candidate_user = make_user(roles=["candidate"], email="cand@fsp.local")
        candidate_user.consent_publish_at = datetime.now(UTC)
        profile = candidate_profile_row(
            user_id=candidate_user.id, full_name="Иванов Иван", phone="+79991234567"
        )
        invitation = invitation_row(company.id, profile.id, status="accepted")
        grant = ContactGrant(
            id=uuid.uuid4(),
            employer_user_id=user.id,
            candidate_id=profile.id,
            basis="invitation",
            ref_id=invitation.id,
            granted_at=datetime.now(UTC),
        )
        session = FakeSession(scalars=[company, invitation, grant, profile, candidate_user])
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/invitations/{invitation.id}/contacts")
        assert response.status_code == 200
        body = response.json()
        assert body["full_name"] == "Иванов Иван"
        assert body["phone"] == "+79991234567"
        assert body["email"] == "cand@fsp.local"
        assert session.added_of(ContactRevealAudit), "раскрытие обязано попадать в 152-ФЗ аудит"
        assert session.added_of(AuditLog)

    def test_contacts_blocked_without_publish_consent(
        self, app: FastAPI, client: TestClient
    ) -> None:
        from app.models.employer import ContactRevealAudit

        user = make_user(roles=["employer"])
        company = company_row(user)
        candidate_user = make_user(roles=["candidate"])
        candidate_user.consent_publish_at = None
        profile = candidate_profile_row(user_id=candidate_user.id)
        invitation = invitation_row(company.id, profile.id, status="accepted")
        grant = ContactGrant(
            id=uuid.uuid4(),
            employer_user_id=user.id,
            candidate_id=profile.id,
            basis="invitation",
            ref_id=invitation.id,
            granted_at=datetime.now(UTC),
        )
        session = FakeSession(scalars=[company, invitation, grant, profile, candidate_user])
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/invitations/{invitation.id}/contacts")
        assert response.status_code == 403
        assert not session.added_of(ContactRevealAudit)
